"""Start Ayla Funding Bot from PyCharm, Windows or Raspberry Pi."""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(PROJECT_DIR)

from dotenv import load_dotenv
load_dotenv(PROJECT_DIR / '.env')


def main():
    import uvicorn
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '8096'))
    print(f'Ayla Funding Bot: http://{host}:{port}')
    uvicorn.run('ayla.app:app', host=host, port=port, reload=False, workers=1)


if __name__ == '__main__':
    main()
