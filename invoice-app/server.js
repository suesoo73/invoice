import crypto from "crypto";
import dotenv from "dotenv";
import express from "express";
import multer from "multer";

dotenv.config();

const app = express();
const port = Number(process.env.PORT || 3000);
const defaultOcrModel = process.env.DEFAULT_OCR_MODEL || "gemini-2.5-flash-lite";
const fetchTimeoutMs = Number(process.env.APP_FETCH_TIMEOUT_MS || 20000);
const longFetchTimeoutMs = Number(process.env.APP_LONG_FETCH_TIMEOUT_MS || 300000);
const upload = multer({ storage: multer.memoryStorage() });
const sessions = new Map();
const sessionTtlMs = Number(process.env.APP_SESSION_TTL_MS || 8 * 60 * 60 * 1000);

app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(express.static("public"));

function normalizeUploadFilename(filename = "") {
  const original = String(filename || "").trim();
  if (!original) {
    return "upload.bin";
  }

  // Browsers and multipart parsers sometimes hand over UTF-8 names as latin1 mojibake.
  if (/[ÃÂÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîï]/.test(original)) {
    try {
      const recovered = Buffer.from(original, "latin1").toString("utf8").trim();
      if (recovered) {
        return recovered;
      }
    } catch {}
  }

  return original;
}

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

function requireOperator(req, res, next) {
  const session = getSession(req);
  if (!session) {
    return res.status(401).json({ detail: "Authentication required" });
  }
  if (!session.user?.is_operator) {
    return res.status(403).json({ detail: "Operator access required" });
  }

  req.appSession = session;
  return next();
}

async function readApiResponse(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text || "응답을 해석하지 못했습니다." };
  }
}

async function fetchMiddleware(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Internal-Token", process.env.MIDDLEWARE_INTERNAL_TOKEN);

  return fetch(`${process.env.MIDDLEWARE_BASE_URL}${path}`, {
    ...options,
    headers,
    signal: options.signal || AbortSignal.timeout(fetchTimeoutMs),
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
  fetchMiddleware("/internal/ocr/operator/llm-config")
    .then(readApiResponse)
    .then((payload) => {
      const config = payload?.config || {};
      res.json({
        default_ocr_model: config.default_model || defaultOcrModel,
        allowed_ocr_models: Array.isArray(config.allowed_models) && config.allowed_models.length
          ? config.allowed_models
          : [defaultOcrModel],
        ocr_backend: config.ocr_backend || "paddleocr_vl",
        ocr_model: config.ocr_model || "",
        allowed_engine_models: Array.isArray(config.ocr_allowed_models) ? config.ocr_allowed_models : [],
        ocr_models_by_backend: config.ocr_models_by_backend || {},
        llm_backend: config.llm_backend || "ollama",
      });
    })
    .catch(() => {
      res.json({
        default_ocr_model: defaultOcrModel,
        allowed_ocr_models: [defaultOcrModel],
        ocr_backend: "paddleocr_vl",
        ocr_model: "",
        allowed_engine_models: [],
        ocr_models_by_backend: {},
        llm_backend: "ollama",
      });
    });
});

app.get("/api/operator/overview", requireSession, async (req, res) => {
  if (!req.appSession.user?.is_operator) {
    return res.status(403).json({ detail: "Operator access required" });
  }

  let appHealth = { status: "error" };
  let middlewareHealth = { status: "error" };
  let operatorPayload = {};

  try {
    appHealth = {
      status: "ok",
      app: "invoice-app",
      uptime_seconds: Math.round(process.uptime()),
      default_ocr_model: defaultOcrModel,
      middleware_base_url: process.env.MIDDLEWARE_BASE_URL,
      server_time: new Date().toISOString(),
    };

    const middlewareHealthResponse = await fetch(`${process.env.MIDDLEWARE_BASE_URL}/health`);
    middlewareHealth = await middlewareHealthResponse.json();

    const overviewResponse = await fetchMiddleware("/internal/ocr/operator/overview?limit=12");
    operatorPayload = await readApiResponse(overviewResponse);

    return res.json({
      app: appHealth,
      middleware: middlewareHealth,
      overview: operatorPayload,
    });
  } catch (error) {
    return res.status(500).json({
      app: appHealth,
      middleware: middlewareHealth,
      detail: String(error),
      overview: operatorPayload,
    });
  }
});

app.get("/api/operator/llm-config", requireOperator, async (_req, res) => {
  const response = await fetchMiddleware("/internal/ocr/operator/llm-config");
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/operator/llm-config", requireOperator, async (req, res) => {
  const response = await fetchMiddleware("/internal/ocr/operator/llm-config", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      llm_backend: req.body.llm_backend,
      default_model: req.body.default_model,
      ocr_backend: req.body.ocr_backend,
      ocr_model: req.body.ocr_model,
      external_llm_api_key: req.body.external_llm_api_key,
    }),
  });
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
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
  try {
    const response = await fetchMiddleware("/internal/local-auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        login_id: req.body.username || "",
        password: req.body.password || "",
      }),
    });
    const payload = await readApiResponse(response);
    if (!response.ok || !payload?.user?.id) {
      return res.status(response.status || 502).json(payload);
    }

    const authUser = payload.user;

    const sessionUser = {
      id: authUser.id,
      company_id: authUser.company_id,
      company_name: authUser.company_name,
      company_code: authUser.company_code,
      company_registration_no: authUser.company_registration_no,
      name: authUser.name,
      email: authUser.email,
      login_id: authUser.login_id,
      is_operator: !!authUser.is_operator,
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
    const message = String(error.message || error);
    const isTimeout = message.includes("timed out") || message.includes("ETIMEDOUT") || message.includes("AbortError");
    return res.status(isTimeout ? 504 : 502).json({
      detail: `Login request failed: ${message}`,
    });
  }
});

app.get("/api/operator/users", requireOperator, async (_req, res) => {
  const response = await fetchMiddleware("/internal/local-auth/users");
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.get("/api/operator/companies/resolve", requireOperator, async (req, res) => {
  const companyId = String(req.query.company_id || "");
  const response = await fetchMiddleware(
    `/internal/local-auth/companies/resolve?company_id=${encodeURIComponent(companyId)}`,
  );
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.get("/api/operator/companies", requireOperator, async (req, res) => {
  const query = String(req.query.query || "");
  const response = await fetchMiddleware(
    `/internal/local-auth/companies?query=${encodeURIComponent(query)}`,
  );
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/operator/companies", requireOperator, async (req, res) => {
  const response = await fetchMiddleware("/internal/local-auth/companies", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(req.body),
  });
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/operator/users", requireOperator, async (req, res) => {
  const response = await fetchMiddleware("/internal/local-auth/users", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(req.body),
  });
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.patch("/api/operator/users/:id", requireOperator, async (req, res) => {
  const response = await fetchMiddleware(`/internal/local-auth/users/${req.params.id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(req.body),
  });
  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
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
  if (req.body.requested_at) {
    form.set("requested_at", req.body.requested_at);
  }
  form.set(
    "file",
    new Blob([req.file.buffer], { type: req.file.mimetype || "application/octet-stream" }),
    normalizeUploadFilename(req.file.originalname),
  );

  const response = await fetchMiddleware("/internal/ocr/uploads", {
    method: "POST",
    body: form,
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/upload-batch", requireSession, upload.array("files"), async (req, res) => {
  const files = Array.isArray(req.files) ? req.files : [];
  if (!files.length) {
    return res.status(400).json({ detail: "files are required" });
  }

  const documentType = req.body.document_type || "invoice";
  const results = [];

  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    const form = new FormData();
    form.set("company_id", req.appSession.user.company_id);
    form.set("requested_by", req.appSession.user.id);
    form.set("document_type", documentType);
    form.set("model_name", req.body.model_name || defaultOcrModel);
    form.set(
      "file",
      new Blob([file.buffer], { type: file.mimetype || "application/octet-stream" }),
      normalizeUploadFilename(file.originalname),
    );

    const response = await fetchMiddleware("/internal/ocr/uploads", {
      method: "POST",
      body: form,
    });
    const payload = await readApiResponse(response);
    results.push({
      index,
      filename: normalizeUploadFilename(file.originalname),
      status_code: response.status,
      ok: response.ok,
      payload,
    });
  }

  const hasFailure = results.some((item) => !item.ok);
  res.status(hasFailure ? 207 : 200).json({
    item_count: results.length,
    results,
  });
});

app.post("/api/documents/manual", requireSession, async (req, res) => {
  const response = await fetchMiddleware("/internal/ocr/documents/manual", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      company_id: req.appSession.user.company_id,
      requested_by: req.appSession.user.id,
      document_type: req.body.document_type || "invoice",
      original_filename: req.body.original_filename || null,
    }),
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

app.get("/api/documents/:id/file", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}`);
  const payload = await readApiResponse(response);

  if (payload?.document?.company_id && payload.document.company_id !== req.appSession.user.company_id) {
    return res.status(403).json({ detail: "Forbidden" });
  }

  return proxyMiddlewareFile(res, `/internal/ocr/documents/${req.params.id}/file`);
});

app.get("/api/documents/:id/preview-image", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}`);
  const payload = await readApiResponse(response);

  if (payload?.document?.company_id && payload.document.company_id !== req.appSession.user.company_id) {
    return res.status(403).json({ detail: "Forbidden" });
  }

  return proxyMiddlewareFile(res, `/internal/ocr/documents/${req.params.id}/preview-image`);
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
      use_grayscale: req.body.use_grayscale !== false,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/documents/:id/reprocess-fields", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/reprocess-fields`, {
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

app.post("/api/documents/:id/ocr-compare", requireSession, async (req, res) => {
  try {
    const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/ocr-compare`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        requested_by: req.appSession.user.id,
        model_name: req.body.model_name,
        use_grayscale: req.body.use_grayscale !== false,
      }),
      signal: AbortSignal.timeout(longFetchTimeoutMs),
    });

    const payload = await readApiResponse(response);
    res.status(response.status).json(payload);
  } catch (error) {
    const message = String(error.message || error);
    const isTimeout = message.includes("timed out") || message.includes("AbortError");
    res.status(isTimeout ? 504 : 502).json({
      detail: isTimeout
        ? "OCR 비교 처리 시간이 길어져 응답이 중단되었습니다. 잠시 후 다시 시도해 주세요."
        : `OCR 비교 요청 실패: ${message}`,
    });
  }
});

app.post("/api/documents/:id/rotate", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/rotate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requested_by: req.appSession.user.id,
      degrees: req.body.degrees,
    }),
  });

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.post("/api/documents/:id/crop", requireSession, async (req, res) => {
  const response = await fetchMiddleware(`/internal/ocr/documents/${req.params.id}/crop`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requested_by: req.appSession.user.id,
      x_ratio: req.body.x_ratio,
      y_ratio: req.body.y_ratio,
      width_ratio: req.body.width_ratio,
      height_ratio: req.body.height_ratio,
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
  const includeTax = req.query.include_tax ?? "true";
  const response = await fetchMiddleware(
    `/internal/reports/summary?company_id=${encodeURIComponent(req.appSession.user.company_id)}&period_type=${encodeURIComponent(periodType)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&include_tax=${encodeURIComponent(includeTax)}`,
  );

  const payload = await readApiResponse(response);
  res.status(response.status).json(payload);
});

app.get("/api/reports/export.xlsx", requireSession, async (req, res) => {
  const periodType = req.query.period_type || "monthly";
  const dateFrom = req.query.date_from || "";
  const dateTo = req.query.date_to || "";
  const includeTax = req.query.include_tax ?? "true";
  await proxyMiddlewareFile(
    res,
    `/internal/reports/export.xlsx?company_id=${encodeURIComponent(req.appSession.user.company_id)}&period_type=${encodeURIComponent(periodType)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&include_tax=${encodeURIComponent(includeTax)}`,
  );
});

app.get("/api/reports/export.pdf", requireSession, async (req, res) => {
  const periodType = req.query.period_type || "monthly";
  const dateFrom = req.query.date_from || "";
  const dateTo = req.query.date_to || "";
  const includeTax = req.query.include_tax ?? "true";
  await proxyMiddlewareFile(
    res,
    `/internal/reports/export.pdf?company_id=${encodeURIComponent(req.appSession.user.company_id)}&period_type=${encodeURIComponent(periodType)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&include_tax=${encodeURIComponent(includeTax)}`,
  );
});

app.listen(port, () => {
  console.log(`invoice-app listening on ${port}`);
});
