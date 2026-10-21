from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any

from azure.identity import get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureBlobKnowledgeSource,
    AzureBlobKnowledgeSourceParameters,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
from azure.search.documents.knowledgebases.models import (
    AzureBlobKnowledgeSourceParams,
    KnowledgeBaseMessage,
    KnowledgeBaseMessageTextContent,
    KnowledgeBaseRetrievalRequest,
    KnowledgeRetrievalLowReasoningEffort,
    KnowledgeRetrievalOutputMode,
    KnowledgeSourceAzureOpenAIVectorizer,
    KnowledgeSourceIngestionParameters,
)
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

from .environment import WorkshopSettings


CLASSIC_INDEX = "dld-rental-rules-classic-rag"
KNOWLEDGE_SOURCE = "dld-rental-rules-blob-source"
KNOWLEDGE_BASE = "dld-rental-rules-knowledge-base"
EMBEDDING_DEPLOYMENT = "text-embedding-3-large"
CHAT_DEPLOYMENT = "gpt-5.4-mini"
POLICY_CONTAINER = "rental-service-policies"
EMBEDDING_DIMENSIONS = 3072


def _embeddings_client(settings: WorkshopSettings, credential: Any) -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=f"https://{settings.foundry_account_name}.openai.azure.com",
        azure_ad_token_provider=get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        ),
        api_version="2024-10-21",
    )


def upload_policy_documents(
    settings: WorkshopSettings,
    credential: Any,
    document_directory: str | Path,
) -> list[str]:
    service = BlobServiceClient(
        account_url=f"https://{settings.storage_name}.blob.core.windows.net",
        credential=credential,
    )
    container = service.get_container_client(POLICY_CONTAINER)
    uploaded = []
    for path in sorted(Path(document_directory).glob("*.pdf")):
        container.upload_blob(path.name, path.read_bytes(), overwrite=True)
        uploaded.append(path.name)
    if not uploaded:
        raise RuntimeError(f"No PDF documents found in {document_directory}")
    return uploaded


def _source_chunks(source_directory: str | Path) -> list[dict[str, Any]]:
    chunks = []
    for path in sorted(Path(source_directory).glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        sections = re.split(r"(?=\n\d+\.\s)", text)
        for index, section in enumerate(sections):
            content = section.strip()
            if content:
                chunks.append(
                    {
                        "id": re.sub(r"[^A-Za-z0-9_-]", "_", f"{path.stem}-{index}"),
                        "content": content,
                        "title": path.stem.replace("_", " ").title(),
                        "source_file": f"{path.stem.lower()}.pdf",
                        "chunk_index": index,
                    }
                )
    return chunks


def create_classic_rag_index(
    settings: WorkshopSettings,
    credential: Any,
    source_directory: str | Path,
) -> int:
    openai = _embeddings_client(settings, credential)
    chunks = _source_chunks(source_directory)
    vectors = openai.embeddings.create(
        model=EMBEDDING_DEPLOYMENT,
        input=[item["content"] for item in chunks],
    ).data
    for item, vector in zip(chunks, vectors):
        item["content_vector"] = vector.embedding

    indexes = SearchIndexClient(settings.search_endpoint, credential)
    indexes.create_or_update_index(
        SearchIndex(
            name=CLASSIC_INDEX,
            fields=[
                SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                SearchableField(name="content", type=SearchFieldDataType.String),
                SearchableField(name="title", type=SearchFieldDataType.String),
                SimpleField(
                    name="source_file",
                    type=SearchFieldDataType.String,
                    filterable=True,
                ),
                SimpleField(
                    name="chunk_index",
                    type=SearchFieldDataType.Int32,
                    filterable=True,
                ),
                SearchField(
                    name="content_vector",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True,
                    vector_search_dimensions=EMBEDDING_DIMENSIONS,
                    vector_search_profile_name="hnsw-profile",
                ),
            ],
            vector_search=VectorSearch(
                algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
                profiles=[
                    VectorSearchProfile(
                        name="hnsw-profile",
                        algorithm_configuration_name="hnsw",
                    )
                ],
            ),
            semantic_search=SemanticSearch(
                configurations=[
                    SemanticConfiguration(
                        name="semantic-config",
                        prioritized_fields=SemanticPrioritizedFields(
                            title_field=SemanticField(field_name="title"),
                            content_fields=[SemanticField(field_name="content")],
                        ),
                    )
                ]
            ),
        )
    )
    search = SearchClient(settings.search_endpoint, CLASSIC_INDEX, credential)
    failed = [result for result in search.upload_documents(chunks) if not result.succeeded]
    if failed:
        raise RuntimeError(f"Classic RAG upload failed: {failed}")
    return len(chunks)


def create_foundry_iq(settings: WorkshopSettings, credential: Any) -> dict[str, Any]:
    """Create a Foundry IQ knowledge base whose knowledge source ingests the PDFs from Blob Storage.

    Search runs its own managed pipeline (extraction, chunking, embeddings, indexing), the same
    one the portal's "Add knowledge source → Azure Blob Storage" flow creates.
    """
    indexes = SearchIndexClient(settings.search_endpoint, credential)
    model_endpoint = f"https://{settings.foundry_account_name}.openai.azure.com"
    source = AzureBlobKnowledgeSource(
        name=KNOWLEDGE_SOURCE,
        description="Synthetic DLD property-service policy PDFs in attendee-owned Blob Storage.",
        azure_blob_parameters=AzureBlobKnowledgeSourceParameters(
            # ResourceId form = keyless: the Search managed identity reads the container.
            connection_string=f"ResourceId={settings.storage_id};",
            container_name=POLICY_CONTAINER,
            ingestion_parameters=KnowledgeSourceIngestionParameters(
                embedding_model=KnowledgeSourceAzureOpenAIVectorizer(
                    azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                        resource_url=model_endpoint,
                        deployment_name=EMBEDDING_DEPLOYMENT,
                        model_name=EMBEDDING_DEPLOYMENT,
                    )
                ),
                disable_image_verbalization=True,
                content_extraction_mode="minimal",
            ),
        ),
    )
    created_source = indexes.create_or_update_knowledge_source(source)
    indexes.create_or_update_knowledge_base(
        KnowledgeBase(
            name=KNOWLEDGE_BASE,
            description="Agentic retrieval for the synthetic DLD property service.",
            knowledge_sources=[KnowledgeSourceReference(name=KNOWLEDGE_SOURCE)],
            retrieval_reasoning_effort=KnowledgeRetrievalLowReasoningEffort(),
            # Agents reason over raw passages (Microsoft's guidance); the notebook comparison
            # requests answer synthesis per call to show a single cited answer.
            output_mode=KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA,
            retrieval_instructions=(
                "Decompose multi-part service questions into the policy concepts needed to "
                "answer status, escalation, evidence, communication, and release decisions."
            ),
            answer_instructions=(
                "Use only retrieved synthetic workshop evidence. Cite reference IDs, distinguish "
                "facts from recommendations, and say when the corpus does not contain an answer."
            ),
            models=[
                KnowledgeBaseAzureOpenAIModel(
                    azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                        resource_url=model_endpoint,
                        deployment_name=CHAT_DEPLOYMENT,
                        model_name=CHAT_DEPLOYMENT,
                    )
                )
            ],
        )
    )
    return created_source.as_dict()


def wait_for_foundry_iq(
    settings: WorkshopSettings,
    credential: Any,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Wait until the Blob knowledge source has completed its first ingestion run."""
    indexes = SearchIndexClient(settings.search_endpoint, credential)
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = indexes.get_knowledge_source_status(KNOWLEDGE_SOURCE).as_dict()
        completed = last.get("lastSynchronizationState")
        if completed:
            processed = completed.get("itemsUpdatesProcessed", 0)
            failed = completed.get("itemsUpdatesFailed", 0)
            if failed:
                raise RuntimeError(f"Foundry IQ ingestion failed for {failed} item(s): {last}")
            return {"status": "ready", "documents_processed": processed, "details": completed}
        print(f"Foundry IQ ingestion: {last.get('synchronizationStatus', 'waiting')}...")
        time.sleep(10)
    raise TimeoutError(f"Foundry IQ ingestion did not complete in time: {last}")


def classic_retrieve(
    settings: WorkshopSettings,
    credential: Any,
    query: str,
    top: int = 5,
) -> list[dict[str, Any]]:
    vector = _embeddings_client(settings, credential).embeddings.create(
        model=EMBEDDING_DEPLOYMENT, input=query
    ).data[0].embedding
    search = SearchClient(settings.search_endpoint, CLASSIC_INDEX, credential)
    results = search.search(
        search_text=query,
        vector_queries=[
            VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=top,
                fields="content_vector",
            )
        ],
        query_type="semantic",
        semantic_configuration_name="semantic-config",
        select=["content", "title", "source_file", "chunk_index"],
        top=top,
    )
    return [dict(item) for item in results]


def agentic_retrieve(
    settings: WorkshopSettings,
    credential: Any,
    query: str,
) -> dict[str, Any]:
    client = KnowledgeBaseRetrievalClient(
        settings.search_endpoint,
        credential,
        knowledge_base_name=KNOWLEDGE_BASE,
    )
    response = client.retrieve(
        KnowledgeBaseRetrievalRequest(
            messages=[
                KnowledgeBaseMessage(
                    role="assistant",
                    content=[
                        KnowledgeBaseMessageTextContent(
                            text=(
                                "Answer only from the synthetic DLD property-service corpus. "
                                "Cite sources and say when evidence is unavailable."
                            )
                        )
                    ],
                ),
                KnowledgeBaseMessage(
                    role="user",
                    content=[KnowledgeBaseMessageTextContent(text=query)],
                ),
            ],
            include_activity=True,
            output_mode=KnowledgeRetrievalOutputMode.ANSWER_SYNTHESIS,
            retrieval_reasoning_effort=KnowledgeRetrievalLowReasoningEffort(),
            knowledge_source_params=[
                AzureBlobKnowledgeSourceParams(
                    knowledge_source_name=KNOWLEDGE_SOURCE,
                    include_references=True,
                )
            ],
        )
    )
    return response.as_dict()
