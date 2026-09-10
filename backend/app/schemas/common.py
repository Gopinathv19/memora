from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base for response schemas read straight off a SQLAlchemy row."""

    model_config = ConfigDict(from_attributes=True)


class DeletedResponse(BaseModel):
    id: str
    deleted: bool = True
