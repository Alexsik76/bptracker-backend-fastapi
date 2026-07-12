from sqlmodel import SQLModel


class ExportResponse(SQLModel):
    message: str
    email: str
