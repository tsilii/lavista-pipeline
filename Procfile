web: streamlit run dashboard.py --server.port $PORT --server.address 0.0.0.0
worker: python ingest.py --schedule
api: uvicorn pos_api:app --host 0.0.0.0 --port $PORT