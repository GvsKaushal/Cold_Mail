import chromadb
import uuid


class Portfolio:
    def __init__(self):
        self.chroma_client = chromadb.Client()
        self.collection = self.chroma_client.get_or_create_collection(name="portfolio")

    def load_portfolio(self, user):
        try:
            for i in range(len(user["portfolio"])):
                self.collection.add(
                    documents=[user["portfolio"][i]["Techstack"]],
                    metadatas={"links": user["portfolio"][i]["Links"]},
                    ids=[str(uuid.uuid4())]
                )

        except Exception as e:
            print(f"Error while loading portfolio: {e}")

    def query_links(self, skills):
        return self.collection.query(query_texts=skills, n_results=2).get('metadatas', [])
