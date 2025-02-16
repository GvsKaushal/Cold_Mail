import chromadb
import uuid


class Portfolio:
    def __init__(self):
        self.chroma_client = chromadb.PersistentClient('vectorstore')
        self.collection = self.chroma_client.get_or_create_collection(name="portfolio")

    def load_portfolio(self, user):
        try:
            user_name = user["username"]
            for portfolio_item in user["portfolio"]:
                techstack = portfolio_item["Techstack"]
                links = portfolio_item["Links"]

                results = self.collection.query(
                    query_texts=[techstack],
                    n_results=1
                )

                existing_entries = []

                metadatas = results.get('metadatas', [])

                for sublist in metadatas:
                    for meta in sublist:
                        if meta.get("user_id") == user_name and meta.get("links") == links:
                            existing_entries.append(meta)

                if not existing_entries:
                    self.collection.add(
                        documents=[techstack],
                        metadatas={"links": links, "user_id": user_name},
                        ids=[str(uuid.uuid4())]
                    )
        except Exception as e:
            print(f"Error while loading portfolio: {e}")

    def query_links(self, skills, user_id):
        try:
            results = self.collection.query(query_texts=skills, n_results=2)

            metadatas = results.get("metadatas", [])
            all_metadata = []

            for sublist in metadatas:
                for meta in sublist:
                    all_metadata.append(meta)

            filtered_links = []
            for meta in all_metadata:
                if meta.get("user_id") == user_id:
                    filtered_links.append(meta["links"])

            return filtered_links
        except Exception as e:
            print(f"Error in query_links: {e}")
            return []
