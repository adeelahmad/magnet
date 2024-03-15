from sentence_transformers import SentenceTransformer

from magnet.utils.globals import _f
from magnet.utils.globals import Utils
from magnet.utils.index.milvus import *
from magnet.utils.data_classes import EmbeddingPayload

from magnet.ic.field import Charge, Prism

from typing import Optional

class Memory:
    """
    The Embedder class is responsible for embedding text using a pre-trained sentence transformer model and storing or sending the embeddings for further processing. It utilizes the Milvus database for storing and searching the embeddings.

    Args:
        config (Config): A Config instance containing the configuration parameters for the Embedder class.
        create (bool, optional): If set to True, a connection to the Milvus database will be created. Defaults to False.

    Attributes:
        config (Config): A Config instance containing the configuration parameters for the Embedder class.
        model (SentenceTransformer): An instance of the SentenceTransformer class from the sentence_transformers library, used for text embedding.
        db (MilvusDB): An instance of the MilvusDB class from the magnet.utils.milvus module, used for connecting to the Milvus database.
    """

    def __init__(self, prism: Prism = None):
        self.config = prism.config
        self._model = None
        
    async def on(self, create: bool = False, initialize: bool = False):
        self._model = SentenceTransformer(self.config.index.model, device=Utils().check_cuda())
        _f('info', f'loading into {self._model.device}')
        self.db = MilvusDB(self.config)
        self.db.on()
        if create:
            self.db.create(overwrite=True)
        self.db.load()
        if initialize:
                self.db.initialize()

    async def index(self, payload, msg, field: Charge = None, v: bool = False, instruction: str = "Represent this sentence for searching relevant passages: "):
        if not msg or not payload:
            return _f('fatal', 'no field message and/or payload to ack!')
        if field:
            self.field = field
        try:
            _f('info', f'encoding payload\n{payload}') if v else None
            payload.embedding = self._model.encode(
                f"{instruction} {payload.text}", normalize_embeddings=True)
        except Exception as e:
            return _f('fatal', e)
        await msg.in_progress()
        try:
            _f('info', f'indexing payload') if v else None
            if not await self.is_dupe(q=payload.embedding):
                self.db.collection.insert([
                    [payload.document], [payload.text], [payload.embedding]
                ])
                if field:
                    payload = EmbeddingPayload(
                        model=self.config.index.model,
                        embedding=self._model.encode(
                            f"{instruction} {payload.text}", normalize_embeddings=True).tolist(),
                        text=payload.text,
                        document=payload.document
                    )
                    _f('info', f'sending payload\n{payload}') if v else None
                    await self.field.pulse(payload)
                await msg.ack_sync()
                _f('success', f'embedding indexed\n{payload}') if v else None
            else:
                await msg.ack_sync()
                _f('warn', f'embedding exists already\n{payload}') if v else None
        except Exception as e:
            await msg.term()
            _f('fatal', e)

    def search(self, payload, limit: int = 100, cb: Optional[callable] = None, instruction: str = "Represent this sentence for searching relevant passages: "):
        payload = EmbeddingPayload(
            text=payload,
            embedding=self._model.encode(
                f"{instruction} {payload}", normalize_embeddings=True),
            model=self.config.index.model,
            document="none"
        )

        _results = self.db.collection.search(
            data=[payload.embedding],
            anns_field="embedding",
            param=self.config.index.options,
            limit=limit,
            output_fields=['text', 'document']
        )
        results = []
        for hits_i, hits in enumerate(_results):
            for hit in hits:
                results.append({
                    'text': hit.entity.get('text'),
                    'document': hit.entity.get('document'),
                    'distance': hit.distance
                })
        if cb:
            return cb(payload.text, results)
        else:
            return results

    async def info(self):
        return self.db.collection

    async def disconnect(self):
        await self.db.off()

    async def delete(self, name: str = None):
        if name and name == self.config.index.name:
            try:
                self.db.delete_index()
            except Exception as e:
                _f('fatal', e)
        else:
            _f('fatal', "name doesn't match the connection or the connection doesn't exist")
    
    async def is_dupe(self, q: str = None):
        match = self.db.collection.search(
            data=[q],
            anns_field="embedding",
            param=self.config.index.options,
            output_fields=['text', 'document'],
            limit=1
        )
        return True if match and match[0] and match[0][0].distance >= 0.99 else False