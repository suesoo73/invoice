import crypto from "crypto";
import dotenv from "dotenv";
import express from "express";
import multer from "multer";

dotenv.config();

const app = express();
const port = Number(process.env.PORT || 3000);
const defaultOcrModel = process.env.DEFAULT_OCR_MODEL || "qwen3.5:9B";
const upload = multer({ storage: multer.memoryStorage() });
const sessions = new Map();
const sessionTtlMs = Number(process.env.APP_SESSION_TTL_MS || 8 * 60 * 60 * 1000);

app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(express.static("public"));

function parseCookies(cookieHeader = "") {
  return Object.fromEntries(
    cookieHeader
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [name, ...rest] = part.split("=");
        return [name, decodeURIComponent(rest.join("="))];
      }),
  );
}

function createSession(user) {
  const token = crypto.randomUUID();
  sessions.set(token, {
    user,
    expiresAt: Date.now() + sessionTtlMs,
  });
  return token;
}

function destroySession(token) {
  if (token) {
    sessions.delete(token);
  }
}

function getSession(req) {
  const cookies = parseCookies(req.headers.cookie || "");
  const token = cookies.invoice_app_session;
  if (!token) {
    return null;
  }

  const session = sessions.get(token);
  if (!session) {
    return null;
  }

  if (session.expiresAt < Date.now()) {
    sessions.delete(token);
    return null;
  }

  return { token, ...session };
}

function requireSession(req, res, next) {
  const session = getSession(req);
  if (!session) {
    return res.status(401).json({ detail: "Authentication required" });
  }

  req.appSession = session;
  return next();
}

async function readApiResponse(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

async function fetchMiddleware(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Internal-Token", process.env.MIDDLEWARE_INTERNAL_TOKEN);

  return fetch(`${process.env.MIDDLEWARE_BASE_URL}${path}`, {
    ...options,
    headers,
  });
}

async function proxyMiddlewareFile(res, path) {
  const response = await fetchMiddleware(path);
  const buffer = Buffer.from(await response.arrayBuffer());
  const contentType = response.headers.get("content-type") || "application/octet-stream";
  const disposition = response.headers.get("content-disposition");

  res.status(response.status);
  res.setHeader("Content-Type", contentType);
  if (disposition) {
    res.setHeader("Content-Disposition", disposition);
  }
  res.send(buffer);
}

async function resolveMappedUser(wpUser) {
  const response = await fetchMiddleware(
    `/internal/auth/resolve-user?wp_user_id=${encodeURIComponent(wpUser.id)}&email=${encodeURIComponent(wpUser.email || "")}`,
  );
  const payload = await readApiResponse(response);

  if (!response.ok) {
    throw new Error(payload.detail || "Mapped app user not found");
  }

  return payload.user;
}

async function fetchWordPressUser(jwtToken) {
  if (!process.env.WORDPRESS_JWT_ME_URL) {
    return null;
  }

  const response = await fetch(process.env.WORDPRESS_JWT_ME_URL, {
    headers: {
      Authorization: `Bearer ${jwtToken}`,
    },
  });

  const payload = await readApiResponse(response);
  return response.ok ? payload : null;
}

app.get("/api/health", async (_req, res) => {
  let middleware = { status: "unreachable" };

  try {
    const response = await fetch(`${process.env.MIDDLEWARE_BASE_URL}/health`);
    middleware = await response.json();
  } catch (error) {
    middleware = { status: "error", detail: String(error) };
  }

  res.json({
    status: "ok",
    app: "invoice-app",
    middleware,
  });
});

app.get("/api/config", (_req, res) => {
  res.json({
    default_ocr_model: defaultOcrModel,
  });
});

app.get("/api/auth/me", (req, res) => {
  const session = getSession(req);
  if (!session) {
    return res.json({ authenticated: false });
  }

  return res.json({
    authenticated: true,
    user: session.user,
    expires_at: new Date(session.expiresAt).toISOString(),
  });
});

app.post("/api/auth/login", async (req, res) => {
  const params = new URLSearchParams();
  params.set("username", req.body.username || "");
  params.set("password", req.body.password || "");

  const response = await fetch(process.env.WORDPRESS_JWT_LOGIN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params,
  });

  const payload = await readApiResponse(response);
  if (!response.ok || !payload?.token || !payload?.user?.id) {
    return res.status(response.status).json(payload);
  }

  const wpUser = (await fetchWordPressUser(payload.token))?.user || payload.user;

  try {
    const mappedUser = await resolveMappedUser({
      id: wpUser.id,
      email: wpUser.email || payload.user.email,
    });

    const sessionUser = {
      id: mappedUser.id,
      company_id: mappedUser.company_id,
      company_name: mappedUser.company_name,
      company_code: mappedUser.company_code,
      name: mappedUser.name,
      email: mappedUser.email,
      wp_user_id: mappedUser.wp_user_id,
      wordpress_login: payload.user.login,
    };

    const sessionToken = createSession(sessionUser);
    res.setHeader(
      "Set-Cookie",
      `invoice_app_session=${encodeURIComponent(sessionToken)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${Math.floor(sessionTtlMs / 1000)}`,
    );

    return res.json({
      success: true,
      token_type: "Session",
      expires_in: Math.floor(sessionTtlMs / 1000),
      user: sessionUser,
    });
  } catch (error) {
    return res.status(403).json({
      detail: String(error.message || error),
    });
  }
});

app.post("/api/auth/logout", (req, res) => {
  const session = getSession(req);
  if (session) {
    destroySession(session.token);
  }

  res.setHeader("Set-Cookie", "invoice_app_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0");
  res.json({ success: true });
});

app.post("/api/demo/enqueue", requireSession, async (req, res) => {
  const response = await fetchMiddleware("/internal/ocr/jobs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      document_id: "33333333-3333-3333-3333-333333333333",
      company_id: req.appSession.user.company_id,
      file_path: "/home/suesoo/invoice-storage/documents/demo-invoice.txt",
      document_type: "invoice",
      requested_by: req.appSession.user.id,
      model_name: req.body.model_name || defaultOcrModel,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/upload", requireSession, upload.single("file"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ detail: "file is required" });
  }

  const form = new FormData();
  form.set("company_id", req.appSession.user.company_id);
  form.set("requested_by", req.appSession.user.id);
  form.set("document_type", req.body.document_type || "invoice");
  form.set("model_name", req.body.model_name || defaultOcrModel);
  form.set(
    "file",
    new Blob([req.file.buffer], { type: req.file.mimetype || "application/octet-stream" }),
    req.file.originalname || "upload.bin",
  );

  const response = await fetchMiddleware("/internal/ocr/uploads", {
    method: "POST",
    body: form,
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.get("/api/documents/:id", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}`);
  const payload = await readApiResponse(response);

  if (payload?.document?.company_id && payload.document.company_id !== req.appSession.user.company_id) {
    return res.status(403).json({ detail: "Forbidden" });
  }

  res.status(response.status).json(payload);
});

app.get("/api/documents", requireSession, async (req, res) => {
  const limit = req.query.limit || "20";
  const trashed = req.query.trashed === "true" ? "true" : "false";
  const response = await fetchMiddleware(
    `/internal/ocr/documents?company_id=${encodeURIComponent(req.appSession.user.company_id)}&limit=${encodeURIComponent(limit)}&trashed=${encodeURIComponent(trashed)}`,
  );

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.patch("/api/documents/:id/review", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/review`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ...req.body,
      requested_by: req.appSession.user.id,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/documents/:id/complete", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/complete`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requested_by: req.appSession.user.id,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/documents/:id/reprocess", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/reprocess`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requested_by: req.appSession.user.id,
      model_name: req.body.model_name,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.delete("/api/documents/:id", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}`, {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requested_by: req.appSession.user.id,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/documents/:id/restore", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/restore`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requested_by: req.appSession.user.id,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.get("/api/reports/summary", requireSession, async (req, res) => {
  const periodType = req.query.period_type || "monthly";
  const dateFrom = req.query.date_from || "";
  const dateTo = req.query.date_to || "";
  const response = await fetchMiddleware(
    `/internal/reports/summary?company_id=${encodeURIComponent(req.appSession.user.company_id)}&period_type=${encodeURIComponent(periodType)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`,
  );

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.get("/api/reports/export.xlsx", requireSession, async (req, res) => {
  const periodType = req.query.period_type || "monthly";
  const dateFrom = req.query.date_from || "";
  const dateTo = req.query.date_to || "";
  await proxyMiddlewareFile(
    res,
    `/internal/reports/export.xlsx?company_id=${encodeURIComponent(req.appSession.user.company_id)}&period_type=${encodeURIComponent(periodType)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`,
  );
});

app.get("/api/reports/export.pdf", requireSession, async (req, res) => {
  const periodType = req.query.period_type || "monthly";
  const dateFrom = req.query.date_from || "";
  const dateTo = req.query.date_to || "";
  await proxyMiddlewareFile(
    res,
    `/internal/reports/export.pdf?company_id=${encodeURIComponent(req.appSession.user.company_id)}&period_type=${encodeURIComponent(periodType)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`,
  );
});

app.listen(port, () => {
  console.log(`invoice-app listening on ${port}`);
});
