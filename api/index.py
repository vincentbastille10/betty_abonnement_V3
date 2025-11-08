# api/index.py
from __future__ import annotations
import os, sys

# autoriser import depuis racine du repo
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import app  # ton objet Flask

# Vercel attend une variable "app" ou "handler" selon l'adaptateur
# Ici, on expose "app" directement.
