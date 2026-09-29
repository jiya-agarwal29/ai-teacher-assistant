import logging

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
model = None


def load_model():
    """
    Loads the sentence-transformer embedding model. Called once from the
    FastAPI lifespan at startup so requests never pay the load cost; safe
    to call again (no-op if already loaded).
    """
    global model
    if model is not None:
        return
    model = SentenceTransformer(_MODEL_NAME)
    logger.info("Loaded embedding model '%s'", _MODEL_NAME)


def create_embedding(text):
    load_model()
    return model.encode([text])[0]

def create_embeddings(texts):
    load_model()
    return model.encode(texts, batch_size=32)
