"""
Some sqlalchemy healpix utility functions/classes
"""

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class SaTarget(Base):
    __tablename__ = "trove_targets_target"
    basetarget_ptr_id = sa.Column(sa.Integer, primary_key=True)
    healpix = sa.Column(sa.BigInteger)
