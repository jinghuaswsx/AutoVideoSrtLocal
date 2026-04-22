"""Unified background task launcher for Flask-SocketIO deployments."""

from web.extensions import socketio


def start_background_task(target, *args, **kwargs):
    """Launch a background task using the active Socket.IO async backend."""
    return socketio.start_background_task(target, *args, **kwargs)
