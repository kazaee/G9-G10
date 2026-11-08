"""One interface for every iLO generation; pick the driver with connect()."""
from .base import IloDriver
from .detect import detect, connect

__all__ = ['IloDriver', 'detect', 'connect']
