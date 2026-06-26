"""
FastAPI entry point — serves the EV Charging Optimization web app.
"""
import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

from api import router as api_router
from data import load_datasets

load_dotenv()

app = FastAPI(title="EV Charging Optimization System")

# Mount static assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# Load data at startup
@app.on_event("startup")
async def startup():
    load_datasets()


# Serve index page
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    mapbox_token = os.getenv("MAPBOX_PUBLIC_TOKEN", "")
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"mapbox_token": mapbox_token},
    )


# Include API routes
app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
