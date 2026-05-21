from sqlalchemy import Column, Integer, String, Text
from pgvector.sqlalchemy import Vector
from backend.models.database import Base


class NeighborhoodDoc(Base):
    __tablename__ = "neighborhood_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    barrio = Column(String(100), nullable=False, index=True)
    distrito = Column(String(100), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=False)

    def __repr__(self) -> str:
        return f"<NeighborhoodDoc(barrio={self.barrio!r}, distrito={self.distrito!r})>"
