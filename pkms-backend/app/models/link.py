"""
Link Model for URL Bookmarks and Web Resources
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.models.base import Base
from app.config import nepal_now


class Link(Base):
    """Link model for storing web bookmarks and URLs"""
    
    __tablename__ = "links"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    url = Column(String(2000), nullable=False)
    description = Column(Text, nullable=True)
    # tags removed - now uses proper many-to-many relationship via link_tags table
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_favorite = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=nepal_now())
    
    # Relationships
    user = relationship("User")
    tag_objs = relationship("Tag", secondary="link_tags", back_populates="links")
    
    def __repr__(self):
        return f"<Link(id={self.id}, title='{self.title}', url='{self.url[:50]}')>" 