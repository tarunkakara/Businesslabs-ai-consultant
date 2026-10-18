"""WSGI / ASGI Entrypoint for Render and Production Deployment."""

from server import app

# Standard aliases for deployment hosts
application = app

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
