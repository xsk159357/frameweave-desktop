"""后端启动入口。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("FRAMEWEAVE_PORT", "8788"))
    from app.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
