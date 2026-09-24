"""Start the news site:  python run.py   then open the printed URL."""
import socket
import sys
from pathlib import Path

import uvicorn

from app.config import load_config


def port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    server = load_config()["server"]
    host, port = server.get("host", "127.0.0.1"), server.get("port", 8000)
    if port_in_use(host, port):
        sys.exit(
            f"\n  A porta {port} já está em uso — o site provavelmente já está a correr"
            f" noutra janela (abra http://localhost:{port}).\n"
            f"  Para ver o que está a usar a porta:  lsof -i :{port}\n"
            f"  Ou mude 'port' em config.yaml.\n")
    print(f"\n  Notícias a correr em  http://localhost:{port}\n  (Ctrl+C para parar)\n")
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning",
                # picks up edits to the code without a manual restart
                reload=server.get("reload", True),
                reload_dirs=[str(Path(__file__).parent / "app")])
