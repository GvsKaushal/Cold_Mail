import os
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.exceptions import OutputParserException
from dotenv import load_dotenv

load_dotenv()


class Chain:
    def __init__(self):
        self.llm = ChatGroq(temperature=0,
                            groq_api_key=os.getenv("GROQ_API_KEY"),
                            model_name="llama-3.3-70b-versatile")

    def extract_jobs(self, cleaned_text):
        prompt_extract = PromptTemplate.from_template(
            """
            ### SCRAPED TEXT FROM WEBSITE:
            {page_data}
            ### INSTRUCTION:
            The scraped text is from the career's page of a website.
            Your job is to extract the job postings and return them in JSON format containing the following keys: `role`, `experience`, `skills` and `description`.
            Only return the valid JSON.
            ### VALID JSON (NO PREAMBLE):
            """
        )
        chain_extract = prompt_extract | self.llm
        res = chain_extract.invoke(input={"page_data": cleaned_text})
        try:
            json_parser = JsonOutputParser()
            res = json_parser.parse(res.content)
        except OutputParserException:
            raise OutputParserException("Context too big. Unable to parse jobs.")
        return res if isinstance(res, list) else [res]

    def write_mail(self, job, links, name, position, company):
        prompt_email = PromptTemplate.from_template(
            """
            
            ### JOB DESCRIPTION:  
            {job_description}  
            
            ### INSTRUCTION:  
            You are {name}, a {position} at {company}, a leading Software Consulting firm. Your task is to write a **concise, engaging, and personalized cold email** to the hiring manager based on the job description above.  
            
            The email should:  
            
            - **Start with a subject line** relevant to the role.  
            - **Address the hiring manager by name** (if available) and reference the job title & company.  
 
            - **Position {company} as a strong partner** for fulfilling their needs, offering contract-based specialists or dedicated teams.  
            
            - **Showcase relevant past projects in next paragraph ** by selecting the most applicable links from {link_list} (no duplicates) and formatting them as follows:  
            
              * [Technology/Service 1]: [Portfolio Link 1]  
              * [Technology/Service 2]: [Portfolio Link 2]  
              * [Technology/Service 3]: [Portfolio Link 3]  
            
            - **Be natural, professional, and to the point**—no unnecessary details or generic sales talk.  
            - **End with a clear and friendly call to action** encouraging a discussion.  
            - **Do not generate any email address**.  
            
            Do not include a preamble.  
            
            ### EMAIL (NO PREAMBLE):  
            
            """
        )
        chain_email = prompt_email | self.llm
        res = chain_email.invoke(
            {"job_description": str(job), "link_list": links, "name": name, "position": position, "company": company})
        return res.content


if __name__ == "__main__":
    print()
