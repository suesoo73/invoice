from pydantic import BaseModel, ConfigDict


class LocalLoginRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    login_id: str
    password: str


class LocalUserCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    company_id: str
    login_id: str
    password: str
    name: str
    email: str
    is_operator: bool = False


class LocalUserUpdateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    company_id: str
    login_id: str
    password: str | None = None
    name: str
    email: str
    is_operator: bool = False
    status: str = "active"


class LocalCompanyCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    registration_no: str
    name: str
