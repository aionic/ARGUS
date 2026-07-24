# 🏛️ ARGUS Architecture

This document describes the **solution flow** and **Azure architecture** of ARGUS
(Automated Retrieval and GPT Understanding System), a cloud-native document
intelligence platform. It complements the high-level overview in the
[README](../README.md) with detailed diagrams derived directly from the source
tree (`src/`, `frontend-next/`, `infra/`).

> All diagrams are rendered with [Mermaid](https://mermaid.js.org/). GitHub renders
> them natively; in VS Code use the built-in Markdown preview.

---

## 1. System Context

Who and what interacts with ARGUS, and through which surface.

```mermaid
graph TB
    user([👤 Business User])
    uploader([👤 Uploader / Automation])
    aiClient([🤖 AI Assistant<br/>Copilot / Claude])

    subgraph ARGUS["👁️ ARGUS Platform (Azure)"]
        fe[Next.js Frontend<br/>ca-frontend]
        be[FastAPI Backend<br/>ca-argus]
        mcp[MCP Server<br/>/mcp]
    end

    user -->|Browse, review, chat| fe
    uploader -->|Upload PDFs / images| fe
    aiClient -->|Streamable HTTP MCP| mcp

    fe -->|Proxy API calls| be
    mcp -.->|Hosted in| be

    be -->|Extract, evaluate, summarize| ai[(Azure AI Services)]
    be -->|Persist results| store[(Cosmos DB + Blob)]

    style ARGUS fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style ai fill:#e0f2f1,stroke:#00695c,stroke-width:2px
    style store fill:#e0f7fa,stroke:#0097a7,stroke-width:2px
```

---

## 2. Azure Architecture (Network & Services)

Every backing service is reachable **only** through private endpoints inside a
dedicated VNet. Container Apps run in a delegated subnet; all other services sit
behind private endpoints in a second subnet, resolved via Private DNS zones.

```mermaid
graph TB
    subgraph RG["📦 Resource Group: rg-argus-dev"]
        direction TB

        subgraph VNET["🌐 VNet 10.0.0.0/16"]
            direction TB

            subgraph SNET_CA["snet-container-apps 10.0.0.0/21 (delegated)"]
                CAE[Container Apps Environment]
                BE[🚀 Backend<br/>ca-argus · FastAPI · :8000<br/>ingress: external]
                FE[📱 Frontend<br/>ca-frontend · Next.js<br/>ingress: external]
                PADDLE[🔎 PaddleOCR<br/>ca-argus-paddleocr<br/>ingress: internal]
                CAE --- BE
                CAE --- FE
                CAE --- PADDLE
            end

            subgraph SNET_PE["snet-private-endpoints 10.0.8.0/24"]
                PE_BLOB[PE: Blob]
                PE_COSMOS[PE: Cosmos]
                PE_DI[PE: Doc Intelligence]
                PE_AI[PE: AI Foundry / OpenAI]
                PE_KV[PE: Key Vault]
            end

            DNS[Private DNS Zones<br/>blob · documents · cognitiveservices<br/>openai · services.ai · vaultcore]
        end

        MI[[🔑 User-Assigned<br/>Managed Identity]]
        ACR[(🏗️ Container Registry<br/>Basic)]
        LAW[📊 Log Analytics]
        APPI[📈 App Insights]

        subgraph EVENT["Event-driven ingestion"]
            EGST[Event Grid<br/>System Topic]
            LOGIC[Logic App<br/>logic-argus-v2]
        end
    end

    STORAGE[(📁 Storage Account<br/>datasets container<br/>shared key disabled)]
    COSMOS[(🗄️ Cosmos DB<br/>doc-extracts<br/>documents + configuration)]
    DI[📄 Document Intelligence]
    FOUNDRY[🧠 AI Foundry account + project<br/>gpt-5.4 · gpt-4.1-mini · embeddings]
    KV[🔐 Key Vault]

    PE_BLOB -. private link .- STORAGE
    PE_COSMOS -. private link .- COSMOS
    PE_DI -. private link .- DI
    PE_AI -. private link .- FOUNDRY
    PE_KV -. private link .- KV

    BE --> PE_BLOB
    BE --> PE_COSMOS
    BE --> PE_DI
    BE --> PE_AI
    BE --> PE_KV
    BE --> PADDLE
    FE --> BE

    STORAGE --> EGST --> LOGIC -->|POST /api/process-file| BE

    MI -. auth .-> BE
    MI -. auth .-> FE
    ACR -. image pull .-> CAE
    BE --> APPI
    FE --> APPI
    APPI --- LAW

    style RG fill:#f5f5f5,stroke:#9e9e9e,stroke-width:2px
    style VNET fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px
    style SNET_CA fill:#fff3e0,stroke:#f57c00,stroke-width:1px
    style SNET_PE fill:#fce4ec,stroke:#c2185b,stroke-width:1px
    style EVENT fill:#f1f8e9,stroke:#558b2f,stroke-width:1px
```

### Resource inventory

| Resource | Bicep module | Purpose |
|----------|--------------|---------|
| VNet + subnets + Private DNS | `network.bicep` | Network isolation & name resolution |
| User-assigned Managed Identity | `identity.bicep` | Zero-credential service-to-service auth |
| Log Analytics + App Insights | `monitoring.bicep` | Telemetry, logs, distributed tracing |
| Key Vault (PE) | `key-vault.bicep` | Secret storage |
| Storage Account (PE) | `storage.bicep` | `datasets` blob container |
| Cosmos DB (PE) | `cosmos.bicep` | `documents` + `configuration` containers |
| Document Intelligence (PE) | `document-intelligence.bicep` | OCR / layout extraction |
| AI Foundry account + project (PE) | `ai-services.bicep` | GPT-5.4, summary model, embeddings, Content Understanding |
| Container Registry | `container-registry.bicep` | Private container images |
| Container Apps (Env + 3 apps) | `container-apps.bicep` | Backend, frontend, PaddleOCR |
| RBAC role assignments | `role-assignments.bicep` | Least-privilege access |
| Event Grid + Logic App | `event-processing.bicep` | Blob-created → process trigger |

---

## 3. Identity & RBAC

All access is granted via Azure RBAC to a single user-assigned managed identity —
`disableLocalAuth: true` and `allowSharedKeyAccess: false` everywhere.

```mermaid
graph LR
    MI[[User-Assigned<br/>Managed Identity]]
    LOGIC[[Logic App<br/>System Identity]]

    MI -->|Storage Blob Data Contributor| ST[Storage]
    MI -->|Cosmos DB Data Contributor| CB[Cosmos DB]
    MI -->|Cognitive Services User| DI[Doc Intelligence]
    MI -->|Cognitive Services OpenAI User<br/>+ Azure AI User| AI[AI Foundry]
    MI -->|Key Vault Secrets User| KV[Key Vault]
    MI -->|AcrPull| ACR[Container Registry]

    LOGIC -->|Storage Blob Data Reader| ST

    style MI fill:#fff8e1,stroke:#ffa000,stroke-width:2px
    style LOGIC fill:#f1f8e9,stroke:#558b2f,stroke-width:2px
```

---

## 4. Document Ingestion & Event Flow

Documents can enter the pipeline three ways. The automated path uses Event Grid +
Logic App; manual and programmatic paths call the backend directly.

```mermaid
sequenceDiagram
    autonumber
    actor U as Uploader
    participant FE as Next.js Frontend
    participant SAS as SAS URL / az CLI
    participant BLOB as Blob Storage (datasets/)
    participant EG as Event Grid System Topic
    participant LA as Logic App
    participant BE as FastAPI Backend
    participant Q as Background Task Queue

    rect rgb(232, 245, 233)
    note over U,BLOB: Path A — Frontend / SAS upload
    U->>FE: Select dataset + file
    FE->>SAS: Request upload URL
    SAS-->>FE: Pre-signed SAS (PUT)
    FE->>BLOB: PUT file to datasets/{dataset}/{file}
    end

    rect rgb(227, 242, 253)
    note over BLOB,BE: Event-driven trigger
    BLOB->>EG: BlobCreated event
    EG->>LA: Webhook (filtered: datasets/ subdir, depth > 1)
    LA->>BE: POST /api/process-file {filename, dataset, blob_path}
    end

    rect rgb(255, 243, 224)
    note over U,BE: Path B/C — Direct API
    U->>BE: POST /api/process-blob {blob_url, dataset}
    U->>BE: POST /api/blob-created (Event Grid schema)
    end

    BE->>Q: Enqueue process_blob (semaphore-bounded)
    BE-->>U: 202 Queued
    Q->>Q: Run extraction pipeline (§5)
```

---

## 5. Document Processing Pipeline

The core of `blob_processing.process_blob` + `ai_ocr.process`. Content
Understanding is the default extraction backend; the GPT (vision) path is used by
explicit override or as a silent fallback when CU output reads low-quality.

```mermaid
flowchart TD
    A[Blob received] --> B[Write temp file<br/>count pages]
    B --> C[initialize_document → Cosmos]
    C --> D[Resolve tier + effective config<br/>economy / standard / premium]
    D --> E{PDF &gt; max_pages_per_chunk?}
    E -->|Yes| F[Split into page chunks]
    E -->|No| G[Single chunk]
    F --> H
    G --> H

    H{PaddleOCR pre-gate<br/>enabled?}
    H -->|Disabled| K
    H -->|Advisory| I[Flag low quality<br/>continue]
    H -->|Block + bad verdict| J[Short-circuit:<br/>flag, record $0 cost,<br/>route to review] --> Z
    I --> K

    K{Extraction backend}
    K -->|content_understanding<br/>default| CU[get_cu_extraction<br/>OCR + fields in one call]
    K -->|gpt| OCR[run_ocr_processing<br/>Doc Intelligence or Mistral]

    CU --> CUQ{CU output<br/>low quality?}
    CUQ -->|Yes| OCR
    CUQ -->|No| RULES

    OCR --> IMG[prepare_images<br/>optional OpenCV enhance]
    IMG --> DIGATE{DI word-confidence<br/>legibility gate}
    DIGATE -->|Fail| FLAG[Flag for review]
    DIGATE -->|Pass| EXT[run_gpt_extraction<br/>Agent Framework + GPT-5.4]
    EXT --> RULES

    RULES[Rules engine<br/>regex / keyword / positional<br/>reduce schema · routing]
    RULES --> EVAL{enable_evaluation?}
    EVAL -->|Yes| EV[run_gpt_evaluation<br/>per-field confidence]
    EVAL -->|No| SUM
    EV --> SUM
    SUM{enable_summary?}
    SUM -->|Yes| S[run_gpt_summary<br/>cheap model]
    SUM -->|No| COST
    S --> COST

    COST[CostTracker.aggregate<br/>tokens + USD per stage]
    COST --> FLAG
    FLAG --> Z[Upsert document to Cosmos<br/>state.processing_completed = true]

    style J fill:#ffebee,stroke:#c62828
    style FLAG fill:#fff8e1,stroke:#f9a825
    style CU fill:#e8f5e9,stroke:#2e7d32
    style EXT fill:#e0f2f1,stroke:#00695c
    style COST fill:#e0f7fa,stroke:#0097a7
```

### Pipeline stages persisted on the document

Each stage flips a boolean in `state.*` and appends cost telemetry to
`properties.cost`. The document schema tracked in Cosmos:

```mermaid
classDiagram
    class Document {
        +string id
        +string dataset
    }
    class properties {
        +blob_name
        +blob_size
        +num_pages
        +tier
        +cost
        +page_metrics
        +content_understanding_usage
        +content_understanding_chunks
        +flag
        +image_quality
        +extraction_backend_used
    }
    class state {
        +file_landed
        +ocr_completed
        +gpt_extraction_completed
        +gpt_evaluation_completed
        +gpt_summary_completed
        +processing_completed
    }
    class extracted_data {
        +ocr_output
        +gpt_extraction_output
        +gpt_extraction_output_with_evaluation
        +gpt_summary_output
    }
    Document --> properties
    Document --> state
    Document --> extracted_data
```

---

## 6. Consumption: Explore, Review, Chat & MCP

Results are consumed through the frontend (live-updated via SSE) and by AI
assistants through the MCP server.

For Content Understanding documents, `properties.page_metrics` records the CU
meter charge plus contextualization and LLM spend allocated equally across pages
within the same CU analyze chunk. `properties.content_understanding_chunks`
holds the compact chunk evidence (normalized output, field confidence, and CU
markdown) referenced by those page records. This preserves the exact chunk-level
billing input while clearly labeling extraction provenance as chunk-scoped when
CU does not provide reliable field-level page spans.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Next.js Frontend
    participant BE as FastAPI Backend
    participant HUB as SSE change-feed poller
    participant CB as Cosmos DB
    participant AF as Agent Framework
    participant AI as AI Foundry (GPT-5.4)

    U->>FE: Open Explore / Review
    FE->>BE: GET documents / flagged
    BE->>CB: Query documents
    CB-->>BE: Items
    BE-->>FE: Results

    note over HUB,CB: Live updates
    HUB->>CB: Poll change feed
    HUB-->>BE: New/updated docs
    BE-->>FE: Server-Sent Events

    note over U,AI: Chat with a document
    U->>FE: Ask a question
    FE->>BE: POST /api/chat {document_id, message}
    BE->>CB: Load extracted data
    BE->>AF: run_chat(context + question)
    AF->>AI: Chat completion
    AI-->>AF: Answer
    AF-->>BE: Response
    BE-->>FE: Answer
```

### MCP integration

The backend exposes a **Streamable HTTP** MCP endpoint (`/mcp`) so AI assistants
can drive ARGUS. Tools are plain Python callables auto-invoked by the Agent
Framework.

```mermaid
graph LR
    CLIENT[🤖 MCP Client<br/>VS Code / Claude] -->|Streamable HTTP| MCP[/mcp endpoint/]
    MCP --> T1[argus_list_documents]
    MCP --> T2[argus_get_document]
    MCP --> T3[argus_chat_with_document]
    MCP --> T4[argus_search_documents]
    MCP --> T5[argus_list_datasets]
    MCP --> T6[argus_get_dataset_config]
    MCP --> T7[argus_create_dataset]
    MCP --> T8[argus_process_document_url]
    MCP --> T9[argus_get_extraction]
    MCP --> T10[argus_get_upload_url]

    T1 & T2 & T3 & T4 & T9 --> CB[(Cosmos DB)]
    T8 & T10 --> BLOB[(Blob Storage)]
    T3 --> AI[(AI Foundry)]

    style MCP fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px
```

---

## 7. Deployment Topology (azd)

`azd up` provisions all infrastructure with Bicep and builds/pushes three
container images defined in `azure.yaml`.

```mermaid
graph TB
    DEV([👩‍💻 Developer]) -->|azd up| AZD[Azure Developer CLI]

    AZD -->|provision| BICEP[infra/main.bicep<br/>+ modules/]
    AZD -->|package + deploy| BUILD

    subgraph BUILD["Build & push (source → image)"]
        B1[src/containerapp → backend]
        B2[frontend-next → frontend]
        B3[src/paddleocr-service → paddleocr]
    end

    BICEP --> ARM[(Azure Resource Manager)]
    B1 & B2 & B3 --> ACR[(Container Registry)]

    ARM --> RG[📦 rg-argus-dev<br/>all resources]
    ACR -->|image pull via MI| RG

    RG --> OUT[azd outputs<br/>BACKEND_URL · FRONTEND_URL<br/>endpoints · identity IDs]

    style BUILD fill:#fff3e0,stroke:#f57c00,stroke-width:1px
    style RG fill:#f5f5f5,stroke:#9e9e9e,stroke-width:2px
```

| azd service | Source | Host | Ingress |
|-------------|--------|------|---------|
| `backend` | `src/containerapp` (Python) | Container App `ca-argus` | External :8000 |
| `frontend` | `frontend-next` (Next.js) | Container App `ca-frontend` | External |
| `paddleocr` | `src/paddleocr-service` (Docker) | Container App `ca-argus-paddleocr` | Internal |

---

## 8. Technology Summary

| Layer | Technology |
|-------|------------|
| API framework | FastAPI, Uvicorn, Pydantic (Python 3.13, uv) |
| AI orchestration | Microsoft Agent Framework → Azure AI Foundry |
| Models | GPT-5.4 (extraction/chat), gpt-4.1-mini (summary), text-embedding-3-large |
| OCR / extraction | Azure Document Intelligence, Content Understanding, Mistral (optional), PaddleOCR pre-gate |
| Data | Azure Cosmos DB, Azure Blob Storage |
| Frontend | Next.js 15, React, Tailwind CSS, shadcn/ui |
| Eventing | Event Grid system topic, Azure Logic Apps |
| Security | Managed Identity, RBAC, Private Endpoints, Key Vault, VNet |
| Observability | Application Insights, Log Analytics |
| IaC / deploy | Bicep (modular), Azure Developer CLI (azd) |

---

*Generated from the ARGUS codebase. Keep this document in sync with `infra/` and
`src/` when the architecture changes.*
