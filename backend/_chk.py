
import importlib
for m in ['cv2','numpy','fastapi','uvicorn','PIL']:
    try:
        mod = importlib.import_module(m)
        print(m, 'OK', getattr(mod, '__version__', ''))
    except Exception as e:
        print(m, 'MISSING', str(e)[:60])
