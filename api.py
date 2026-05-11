from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ml_engine import get_ml_signal

app = FastAPI()

# Дозволяємо твоєму React-сайту робити запити сюди
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # У продакшені тут буде URL твого сайту
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/predict/{symbol}")
def get_prediction(symbol: str, interval: str = "4h"):
    # Ця функція запустить твій крутий XGBoost, оновить БД і поверне результат
    result = get_ml_signal(symbol, interval)
    return result