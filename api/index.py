"""Vercel entry point: the whole Flask app as one serverless function.

Vercel detects Flask and sends every request here with its original path
(e.g. /api/disease/analyze), so routing works as usual. Local development
keeps using `python run.py`.
"""
import os
import sys

# Make the backend root (which holds the `app` package) importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402

app = create_app()
