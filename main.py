from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from langchain_community.document_loaders import WebBaseLoader
from chains import Chain
from portfolio import Portfolio
from utils import clean_text

# Initialize FastAPI app
app = FastAPI()

# Initialize required objects
chain = Chain()
portfolio = Portfolio()

# Set up templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

class URLInput(BaseModel):
    url: str

@app.post("/generate-email/")
async def generate_email(input_data: URLInput):
    try:
        loader = WebBaseLoader([input_data.url])
        pages = loader.load()
        if not pages:
            raise ValueError("No data found at the provided URL.")
        data = clean_text(pages.pop().page_content)
        portfolio.load_portfolio()
        jobs = chain.extract_jobs(data)
        emails = []
        for job in jobs:
            skills = job.get('skills', [])
            links = portfolio.query_links(skills)
            email = chain.write_mail(job, links)
            emails.append({"job": job, "email": email})
        return {"emails": emails}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
