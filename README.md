# 👁️ ARGUS: The All-Seeing Document Intelligence Platform

<div align="center">

[![Azure](https://img.shields.io/badge/Azure-0078D4?style=for-the-badge&logo=microsoft-azure&logoColor=white)](https://azure.microsoft.com)
[![OpenAI](https://img.shields.io/badge/GPT--5.4-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=next.js&logoColor=white)](https://nextjs.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

*Named after Argus Panoptes, the mythological giant with a hundred eyes—ARGUS never misses a detail in your documents.*

</div>

## 🚀 Transform Document Processing with AI Intelligence

**ARGUS** revolutionizes how organizations extract, understand, and act on document data. By combining the precision of **Azure Document Intelligence** with the contextual reasoning of **GPT-5.4**, ARGUS doesn't just read documents—it *understands* them.

### 💡 Why ARGUS?

Traditional OCR solutions extract text but miss the context. AI-only approaches struggle with complex layouts. **ARGUS bridges this gap**, delivering enterprise-grade document intelligence that:

- **🎯 Extracts with Purpose**: Understands document context, not just text
- **⚡ Scales Effortlessly**: Process thousands of documents with cloud-native architecture
- **🔒 Secures by Design**: Enterprise security with managed identities and RBAC
- **🧠 Learns Continuously**: Configurable datasets adapt to your specific document types
- **📊 Measures Success**: Built-in evaluation tools ensure consistent accuracy

---

## 🌟 Key Capabilities

<table>
<tr>
<td width="50%">

### 🔍 **Intelligent Document Understanding**
- **Hybrid AI Pipeline**: Combines OCR precision with LLM reasoning
- **Multiple OCR Providers**: Azure Document Intelligence or Mistral Document AI
- **Context-Aware Extraction**: Understands relationships between data points
- **Multi-Format Support**: PDFs, images, forms, invoices, medical records
- **Zero-Shot Learning**: Works on new document types without training

### ⚡ **Enterprise-Ready Performance**
- **Cloud-Native Architecture**: Built on Azure Container Apps with VNet integration
- **Scalable Processing**: Handle document floods with confidence
- **Real-Time Processing**: API-driven workflows for immediate results
- **Event-Driven Automation**: Automatic processing on document upload
- **Zero-Credential Security**: Managed identity authentication with no API keys

</td>
<td width="50%">

### 🎛️ **Advanced Control & Customization**
- **Dynamic Configuration**: Runtime settings without redeployment
- **Custom Datasets**: Tailor extraction for your specific needs
- **Interactive Chat**: Ask questions about processed documents
- **Concurrency Management**: Fine-tune performance for your workload

### 📈 **Comprehensive Analytics**
- **Built-in Evaluation**: Multiple accuracy metrics and comparisons
- **Performance Monitoring**: Application Insights integration
- **Custom Evaluators**: Fuzzy matching, semantic similarity, and more
- **Visual Analytics**: Jupyter notebooks for deep analysis

</td>
</tr>
</table>

---

## 🏗️ Architecture: Built for Scale and Security

ARGUS employs a modern, cloud-native architecture designed for enterprise workloads:

> 📐 **Deep dive:** For detailed solution-flow and Azure architecture diagrams
> (network topology, RBAC, ingestion/event flow, processing pipeline, MCP, and
> azd deployment), see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

<div align="center">

```mermaid
graph TB
    subgraph "📥 Document Input"
        A[📄 Documents] --> B[📁 Azure Blob Storage]
        C[🌐 Direct Upload API] --> D[🚀 FastAPI Backend]
    end

    subgraph "🧠 AI Processing Engine"
        B --> D
        D --> E{🔍 OCR Provider}
        E -->|Azure| E1[Azure Document Intelligence]
        E -->|Mistral| E2[Mistral Document AI]
        D --> F[🤖 GPT-5.4]
        E1 --> G[⚙️ Hybrid Processing Pipeline]
        E2 --> G
        F --> G
    end

    subgraph "💡 Intelligence & Analytics"
        G --> H[📊 Custom Evaluators]
        G --> I[💬 Interactive Chat]
        H --> J[📈 Results & Analytics]
    end

    subgraph "💾 Data Layer"
        G --> K[🗄️ Azure Cosmos DB]
        J --> K
        I --> K
        K --> L[📱 Next.js Frontend]
    end

    style A fill:#e3f2fd,stroke:#1976d2,stroke-width:2px
    style B fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style C fill:#e8f5e8,stroke:#388e3c,stroke-width:2px
    style D fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style E fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style E1 fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style E2 fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style F fill:#e0f2f1,stroke:#00695c,stroke-width:2px
    style G fill:#fff8e1,stroke:#ffa000,stroke-width:2px
    style H fill:#f1f8e9,stroke:#558b2f,stroke-width:2px
    style I fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px
    style J fill:#fdf2e9,stroke:#e65100,stroke-width:2px
    style K fill:#e0f7fa,stroke:#0097a7,stroke-width:2px
    style L fill:#f9fbe7,stroke:#827717,stroke-width:2px
```

</div>

### 🔧 Infrastructure Components

| Component | Technology | Purpose |
|-----------|------------|---------|
| **🚀 Backend API** | Azure Container Apps + FastAPI | High-performance document processing engine |
| **📱 Frontend UI** | Next.js (React) | Modern document management interface |
| **📁 Document Storage** | Azure Blob Storage | Secure, scalable document repository |
| **🗄️ Metadata Database** | Azure Cosmos DB | Results, configurations, and analytics |
| **🔍 OCR Engine** | Azure Document Intelligence or Mistral Document AI | Structured text and layout extraction |
| **🧠 AI Reasoning** | Azure AI Foundry (GPT-5.4) via Microsoft Agent Framework | Contextual understanding and extraction |
| **🏗️ Container Registry** | Azure Container Registry | Private, secure container images |
| **🔒 Security** | Managed Identity + RBAC | Zero-credential architecture |
| **🌐 Network** | VNet + Private Endpoints | Network isolation for all Azure services |
| **🔑 Secrets** | Azure Key Vault | Centralized secrets management |
| **📊 Monitoring** | Application Insights | Performance and health monitoring |

---

## 🔒 Security Architecture

ARGUS implements a defense-in-depth security model:

### Network Isolation
- **VNet Integration**: All Container Apps run within a dedicated Virtual Network (`10.0.0.0/16`)
- **Private Endpoints**: Storage, Cosmos DB, OpenAI, Document Intelligence, and Key Vault are accessible only through private endpoints
- **Private DNS Zones**: Automatic DNS resolution for private endpoints via Azure Private DNS
- **No Public Access**: All backend services have `publicNetworkAccess: Disabled`

### Identity & Authentication
- **Managed Identity**: User-assigned managed identity for all service-to-service authentication
- **No API Keys**: Local authentication is disabled on all Azure services (`disableLocalAuth: true`)
- **No Shared Keys**: Storage account shared key access is disabled (`allowSharedKeyAccess: false`)
- **RBAC-Only Access**: All permissions are granted through Azure RBAC role assignments

### RBAC Roles (Principle of Least Privilege)
| Role | Scope | Purpose |
|------|-------|---------|
| Storage Blob Data Contributor | Storage Account | Read/write blob data |
| Cosmos DB Built-in Data Contributor | Cosmos DB Account | Read/write database items |
| Cognitive Services User | Document Intelligence | OCR operations |
| Cognitive Services OpenAI User | Azure OpenAI | Model inference |
| Key Vault Secrets User | Key Vault | Read secrets |
| AcrPull | Container Registry | Pull container images |

---

## ⚡ Quick Start: Deploy in Minutes

### 📋 Prerequisites

<details>
<summary><b>🛠️ Required Tools (Click to expand)</b></summary>

1. **Docker**
   ```bash
   # Install Docker (required for containerization during deployment)
   # Visit https://docs.docker.com/get-docker/ for installation instructions
   ```

2. **Azure Developer CLI (azd)**
   ```bash
   curl -fsSL https://aka.ms/install-azd.sh | bash
   ```

3. **Azure CLI**
   ```bash
   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
   ```

4. **Azure Subscription**
   - An active Azure subscription with permissions to create resources
   - The deployment automatically provisions all required Azure services (OpenAI, Storage, Cosmos DB, etc.)
   - Authentication uses managed identity — no API keys required

</details>

### 🚀 One-Command Deployment

```bash
# 1. Clone the repository
git clone https://github.com/Azure-Samples/ARGUS.git
cd ARGUS

# 2. Login to Azure
az login

# 3. Deploy everything with a single command
azd up
```

**That's it!** 🎉 Your ARGUS instance is now running in the cloud.

### ✅ Verify Your Deployment

```bash
# Check system health
curl "$(azd env get-value BACKEND_URL)/health"

# Expected response:
{
  "status": "healthy",
  "services": {
    "cosmos_db": "✅ connected",
    "blob_storage": "✅ connected",
    "document_intelligence": "✅ connected",
    "azure_openai": "✅ connected"
  }
}

# View live application logs
azd logs --follow
```

---

## 🎮 Usage Examples: See ARGUS in Action

### 📄 Method 1: Upload via Frontend Interface (Recommended)

The easiest way to process documents is through the user-friendly web interface:

1. **Access the Frontend**:
   ```bash
   # Get the frontend URL after deployment
   azd env get-value FRONTEND_URL
   ```

2. **Upload and Process Documents**:
   - Navigate to the **"🧠 Process Files"** tab
   - Select your dataset from the dropdown (e.g., "default-dataset", "medical-dataset")
   - Use the **file uploader** to select PDF, image, or Office documents
   - Click **"Submit"** to upload files
   - Files are automatically processed using the selected dataset's configuration
   - Monitor processing status in the **"🔍 Explore Data"** tab

### 📤 Method 2: Direct Blob Storage Upload

For automation or bulk processing, upload files directly to Azure Blob Storage:

```bash
# Upload a document to be processed automatically
az storage blob upload \
  --account-name "$(azd env get-value STORAGE_ACCOUNT_NAME)" \
  --container-name "datasets" \
  --name "default-dataset/invoice-2024.pdf" \
  --file "./my-invoice.pdf" \
  --auth-mode login

# Files uploaded to blob storage are automatically detected and processed
# Results can be viewed in the frontend or retrieved via API
```

### 💬 Example 3: Interactive Document Chat

Ask questions about any processed document through the API:

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "blob_url": "https://mystorage.blob.core.windows.net/datasets/default-dataset/contract.pdf",
    "question": "What are the key terms and conditions in this contract?"
  }' \
  "$(azd env get-value BACKEND_URL)/api/chat"

# Get intelligent answers:
{
  "answer": "The key terms include: 1) 12-month service agreement, 2) $5000/month fee, 3) 30-day termination clause...",
  "confidence": 0.91,
  "sources": ["page 1, paragraph 3", "page 2, section 2.1"]
}
```

---

## 🤖 MCP Integration: AI-Powered Document Access

ARGUS supports the **Model Context Protocol (MCP)** using the modern **Streamable HTTP transport**, enabling AI assistants like GitHub Copilot, Claude, and other MCP-compatible clients to interact directly with your document intelligence platform.

### 🔌 What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io/) is an open standard that allows AI assistants to securely connect to external data sources and tools. With ARGUS MCP support, your AI assistant can:

- 📄 **List and search documents** across all your datasets
- 🔍 **Query document content** and extracted data
- 💬 **Chat with documents** using natural language
- 📤 **Upload new documents** for processing
- ⚙️ **Manage datasets** and configurations

### ⚡ Quick Setup

Add ARGUS to your MCP client configuration:

**VS Code / GitHub Copilot** (`~/.vscode/mcp.json` or workspace settings):
```json
{
  "mcpServers": {
    "argus": {
      "url": "https://<your-backend-url>/mcp"
    }
  }
}
```

> **Tip**: After deployment with `azd up`, get your backend URL from the Azure Portal or run `azd show` to find the Container App URL.

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "argus": {
      "url": "https://<your-backend-url>/mcp"
    }
  }
}
```

> **Note**: ARGUS uses the Streamable HTTP transport (the modern MCP standard). The endpoint is a single `/mcp` path that handles all MCP communication.

### 🛠️ Available MCP Tools

| Tool | Description |
|------|-------------|
| `argus_list_documents` | List all processed documents with filtering options |
| `argus_get_document` | Get detailed document information including OCR and extraction results |
| `argus_chat_with_document` | Ask natural language questions about a document |
| `argus_search_documents` | Search documents by keyword across all datasets |
| `argus_list_datasets` | List available dataset configurations |
| `argus_get_dataset_config` | Get system prompt and schema for a dataset |
| `argus_create_dataset` | Create a new dataset with custom prompt and schema |
| `argus_process_document_url` | Queue a document for processing from blob URL |
| `argus_get_extraction` | Get extracted structured data from a document |
| `argus_get_upload_url` | Get a pre-signed SAS URL for direct document upload |

### 💡 Example Interactions

Once configured, you can interact with ARGUS through your AI assistant:

```
User: "Show me all invoices processed in the last week"
AI: [Uses argus_list_documents to retrieve recent invoices]

User: "What's the total amount on invoice INV-2024-001?"
AI: [Uses argus_get_document to fetch invoice details]

User: "I need to upload a new contract for processing"
AI: [Uses argus_get_upload_url to get a secure upload link]

User: "Compare the extraction results between these two invoices"
AI: [Uses argus_get_extraction on both documents and compares]

User: "Create a new dataset for processing purchase orders"
AI: [Uses argus_create_dataset with appropriate prompt and schema]
```


---

## 🎛️ Advanced Configuration

### 📊 Dataset Management

ARGUS uses **datasets** to define how different types of documents should be processed. A dataset contains:
- **Model Prompt**: Instructions telling the AI how to extract data from documents
- **Output Schema**: The target structure for extracted data (can be empty to let AI determine the structure)
- **Processing Options**: Settings for OCR, image analysis, summarization, and evaluation

**When to create custom datasets**: Create a new dataset when you have a specific document type that requires different extraction logic than the built-in datasets (e.g., contracts, medical reports, financial statements).

<details>
<summary><b>🗂️ Built-in Datasets</b></summary>

- **`default-dataset/`**: Invoices, receipts, general business documents
- **`medical-dataset/`**: Medical forms, prescriptions, healthcare documents

</details>

<details>
<summary><b>🔧 Create Custom Datasets</b></summary>

Datasets are managed through the web frontend interface (deployed automatically with azd):

1. **Access the frontend** (URL provided after azd deployment)
2. **Navigate to the Process Files tab**
3. **Scroll to "Add New Dataset" section**
4. **Configure your dataset**:
   - Enter dataset name (e.g., "legal-contracts")
   - Define model prompt with extraction instructions
   - Specify output schema (JSON format) or leave empty
   - Set processing options (OCR, images, evaluation)
5. **Click "Add New Dataset"** - it's saved directly to Cosmos DB

</details>

---

### � OCR Provider Configuration

ARGUS supports **two OCR providers** for document text extraction:

- **Azure Document Intelligence** (Default): Microsoft's enterprise OCR service with advanced layout understanding
- **Mistral Document AI**: Mistral's document processing service with markdown-optimized output

<details>
<summary><b>🔧 Configure OCR Provider</b></summary>

**Via Frontend (Recommended)**:
1. Navigate to **Settings** tab in the web interface
2. Select **OCR Provider** section
3. Choose your provider:
   - **Azure**: Uses Azure Document Intelligence (automatically configured during deployment)
   - **Mistral**: Requires additional configuration (endpoint, API key, model name)
4. For Mistral, enter:
   - **Mistral Endpoint**: Your Mistral Document AI API endpoint URL
   - **Mistral API Key**: Your Mistral API authentication key
   - **Mistral Model**: Model name (default: `mistral-document-ai-2505`)
5. Click **"Update OCR Provider"** to apply changes

**Via Environment Variables**:
Set the following environment variables in your deployment:

```bash
# Choose OCR provider
OCR_PROVIDER=mistral  # or "azure" (default)

# Mistral-specific configuration (only needed if OCR_PROVIDER=mistral)
MISTRAL_DOC_AI_ENDPOINT=https://your-endpoint.services.ai.azure.com/providers/mistral/azure/ocr
MISTRAL_DOC_AI_KEY=your-mistral-api-key
MISTRAL_DOC_AI_MODEL=mistral-document-ai-2505
```

**Update via Azure Portal**:
1. Navigate to Azure Portal → Container Apps → Your Backend App
2. Go to **Settings** → **Environment variables**
3. Add/update the variables listed above
4. **Restart** the container app

**Update via Azure CLI**:
```bash
# Switch to Mistral
az containerapp update \
  --name <your-backend-app-name> \
  --resource-group <your-resource-group> \
  --set-env-vars \
    OCR_PROVIDER="mistral" \
    MISTRAL_DOC_AI_ENDPOINT="https://your-endpoint.../ocr" \
    MISTRAL_DOC_AI_KEY="your-api-key" \
    MISTRAL_DOC_AI_MODEL="mistral-document-ai-2505"

# Switch back to Azure
az containerapp update \
  --name <your-backend-app-name> \
  --resource-group <your-resource-group> \
  --set-env-vars OCR_PROVIDER="azure"
```

**Note**: OCR provider selection is configured at the solution level and applies to all document processing operations.

</details>

---

### 🧩 Extraction Backend: Content Understanding

In addition to the default **GPT (vision)** extraction path, ARGUS supports **Azure AI Content Understanding (CU)** as a selectable, schema-driven extraction backend. CU performs OCR **and** typed field extraction in a single call directly from the dataset schema, and returns per-field confidence scores.

- **Selectable at two levels**:
  - **Solution-wide default** via the `EXTRACTION_BACKEND` env var (`gpt` | `content_understanding`, default `gpt`).
  - **Per-dataset override** via `processing_options.extraction_backend` (set from the dataset settings UI or the `/api/configuration` endpoint).
- When CU is active, GPT evaluation is skipped by default (CU returns its own confidence); summary still runs.

```bash
# Solution-wide default
EXTRACTION_BACKEND=content_understanding   # or "gpt" (default)

# Content Understanding configuration (resource data-plane)
AZURE_CONTENT_UNDERSTANDING_ENDPOINT=https://<aiservices-account>.cognitiveservices.azure.com/
CONTENT_UNDERSTANDING_API_VERSION=2025-11-01
CONTENT_UNDERSTANDING_COMPLETION_MODEL=gpt-4.1-mini          # resource default completion model
CONTENT_UNDERSTANDING_EMBEDDING_MODEL=text-embedding-3-large # resource default embedding model
```

> **CU data-plane setup notes**: CU requires the AI Services account to have **resource defaults** set before custom analyzers can be created — both a **completion** model (`gpt-4.1-mini`) and an **embedding** model (`text-embedding-3-large`) must be deployed at the account. ARGUS sets these defaults automatically on first CU use (idempotent `PATCH /contentunderstanding/defaults`, body `{"modelDeployments": {...}}`). The managed identity needs the **Cognitive Services User** role. Custom analyzer ids may contain only `[a-zA-Z0-9._]` (no hyphens), and the only valid base analyzers are `prebuilt-document`, `prebuilt-image`, `prebuilt-audio`, `prebuilt-video`. All of this is provisioned by Bicep (`infra/modules/ai-services.bicep` deploys the embedding model; `role-assignments.bicep` grants the role).

---

### 🖼️ Image Quality Pre-processing (OpenCV)

To avoid burning tokens on unreadable pages, ARGUS can pre-screen and auto-enhance page images before extraction (powered by `opencv-python-headless`). The flow is **assess → auto-enhance (deskew/denoise/CLAHE/upscale) → re-assess → flag if still bad**.

```bash
ENABLE_IMAGE_PREPROCESSING=true   # default false
```

Per-dataset knobs are available via `processing_options`: `enable_preprocessing`, `enable_enhancement`, `skip_if_still_bad` (token-saving; skip hopeless pages), and `quality_thresholds`. Per-page metrics (blur variance, brightness/contrast, effective DPI, skew, `enhanced`, `flagged_low_quality`) are stored on the document under `properties.image_quality`.

---

### 💸 Cost-Effective Summary Model

The **summary** stage (text-only) can be routed to a cheaper model than the main extraction deployment, for cost savings:

```bash
SUMMARY_MODEL_DEPLOYMENT_NAME=gpt-4.1-mini   # empty = use main deployment
```

Bicep deploys `gpt-4.1-mini` (Standard SKU) by default (`deploySummaryModel=true`). An optional **Phi-4** serverless deployment is available as a cost experiment (`deployPhiModel=true`, gated off by default — verify regional availability first). The per-call model override lives in `ai_ocr/agents/client.py` (per-deployment client cache).

---

## 🧠 Extraction Controls, Cost Intelligence & Review

ARGUS now includes tiered extraction presets, deterministic rules, per-document cost telemetry, profiling reports, and a human review workflow for flagged documents.

### 🎚️ Extraction Tiers

Choose a tier per dataset with `processing_options.tier`; if omitted, ARGUS uses `DEFAULT_EXTRACTION_TIER` (`standard` by default).

| Tier | Best for | Cost/quality trade-off |
|------|----------|------------------------|
| **Economy** | Simple, short, text-readable documents | Lowest cost: OCR + rules-first extraction, no page images, evaluation, or summary by default |
| **Standard** | Routine production extraction | Balanced cost/quality: OCR + images with rules-first schema reduction |
| **Premium** | High-value or complex documents | Highest quality: full extraction plus evaluation and summary using the configured premium/default models |

Advanced overrides can be set in the same `processing_options` object when a dataset needs to deviate from the preset:

```json
{
  "processing_options": {
    "tier": "standard",
    "enable_images": true,
    "enable_evaluation": false,
    "enable_summary": false,
    "extraction_model": "gpt-4.1-mini",
    "summary_model": "gpt-4.1-mini",
    "use_rules_engine": true
  }
}
```

The resolved tier is persisted on each document as `properties.tier`.

### 🧭 Dynamic Rules Engine

Rules run after OCR and before the LLM. They can fill schema fields deterministically, shrink the schema passed to the LLM, or skip the LLM entirely when all required fields are confidently filled.

```json
{
  "processing_options": {
    "rules": {
      "invoice_number": {"type": "regex", "pattern": "invoice_number", "required": true},
      "vendor_name": {"type": "keyword", "keywords": ["Vendor", "Supplier"]},
      "total_amount": {"type": "regex", "pattern": "amount"}
    },
    "routing_thresholds": {
      "low_quality_page_fraction": 0.5,
      "low_quality_min_pages": 1,
      "min_ocr_text_length": 20,
      "economy_max_pages": 1
    }
  }
}
```

Supported extractor types are **regex**, **keyword**, and **positional**. Routing can pre-flag low-quality or unreadable documents, route them to review, skip extraction, or auto-select Economy for small readable documents. Defaults can be tuned with `ROUTING_LOW_QUALITY_PAGE_FRACTION`, `ROUTING_LOW_QUALITY_MIN_PAGES`, `ROUTING_MIN_OCR_TEXT_LENGTH`, and `ROUTING_ECONOMY_MAX_PAGES`.

### 💰 Cost Telemetry

Every processed document receives `properties.cost` with stage-level tokens and USD:

```json
{
  "per_stage": [
    {"stage": "extraction", "model": "gpt-4.1-mini", "input_tokens": 1200, "output_tokens": 240, "usd": 0.0012}
  ],
  "total_input_tokens": 1200,
  "total_output_tokens": 240,
  "total_usd": 0.0012,
  "usd_per_page": 0.0006,
  "pricing_source": "azure_retail",
  "model_breakdown": {"gpt-4.1-mini": 0.0012}
}
```

Pricing is resolved through the Azure Retail Prices API and cached where available. Set `PRICING_USE_RETAIL_API=false` to force the bundled `pricing_fallback.json` prices; mixed/fallback pricing is reflected in `pricing_source`.

### 📊 Cost Profiling

Run sample documents across tiers to compare cost, tokens, success rate, failures, and quality distribution. The backend endpoint persists a Cosmos DB document with `type: "profiling_report"`.

```bash
# API
curl -X POST "$BACKEND_URL/api/profiling/run" \
  -H "Content-Type: application/json" \
  -d '{"dataset":"default-dataset","tiers":["economy","standard","premium"]}'

# CLI harness from repo root
uv run --project src\containerapp python scripts\cost_profile.py --dataset default-dataset --tiers economy,standard,premium
```

Reports return `{per_tier, runs, generated_at}` with `avg_usd_per_page`, `avg_tokens`, `success_rate`, `failure_types`, and `quality_dist` per tier.

### 🚩 Flagged-Document Review & Email (MOCK)

Documents that fail preflight, sanity, routing, or image-quality checks are marked with:

```json
{
  "properties": {
    "flag": {
      "flagged": true,
      "reasons": ["low_quality_pages=1/2 (fraction=0.50)"],
      "stage": "quality",
      "flagged_at": "2026-06-23T21:31:05Z",
      "email": {"sent_mock": true}
    }
  }
}
```

Use the Next.js **Review** screen (`/review`) to list flagged documents, generate an editable email draft, and perform a clearly labeled **MOCK send**. No real email is delivered; the send endpoint only logs the action and persists metadata under `properties.flag.email`.

Endpoints:
- `GET /api/documents/flagged`
- `POST /api/documents/{id}/flag-email/generate`
- `POST /api/flag-email/send` (**MOCK**, no delivery)

Email defaults are configured with `FLAG_EMAIL_FROM` and `FLAG_EMAIL_TO_FALLBACK`. The prompt template is stored in configuration under `flag_email.prompt_template` and defaults to `DEFAULT_FLAG_EMAIL_PROMPT_TEMPLATE`.

---

The **Next.js** frontend is **automatically deployed** with `azd up` (as `ca-frontend`) and provides a user-friendly interface for document management.

<div align="center">
<img src="docs/ArchitectureOverview.png" alt="ARGUS Frontend Interface" width="800"/>
</div>

### 🎯 Frontend Features

| Tab | Functionality |
|-----|---------------|
| **🧠 Process Files** | Drag-and-drop document upload, tier selection, advanced extraction toggles |
| **🔍 Explore Data** | Browse processed documents, search results, extraction details, and cost aggregates |
| **🚩 Review** | Triage flagged documents and mock-send editable re-upload email drafts |
| **⚙️ Settings** | Configure datasets, adjust processing parameters, manage connections |
| **📋 Instructions** | Interactive help, API documentation, and usage examples |

---

## ️ Development & Customization

### 🏗️ Project Structure Deep Dive

```
ARGUS/
├── 📋 azure.yaml                        # Azure Developer CLI configuration
├── 📄 README.md                         # Project documentation & setup guide
├── 📄 LICENSE                           # MIT license file
├── 📄 CONTRIBUTING.md                   # Contribution guidelines
├── 📄 sample-invoice.pdf                # Sample document for testing
├── 🔧 .env.template                     # Environment variables template
├── 📂 .github/                          # GitHub Actions & workflows
├── 📂 .devcontainer/                    # Development container configuration
├── 📂 .vscode/                          # VS Code settings & extensions
│
├── 📂 infra/                            # 🏗️ Azure Infrastructure as Code
│   ├── ⚙️ main.bicep                    # Orchestrator Bicep template (calls modules)
│   ├── ⚙️ main.parameters.json          # Infrastructure parameters & configuration
│   ├── ⚙️ main-containerapp.bicep       # Container App specific infrastructure
│   ├── ⚙️ main-containerapp.parameters.json # Container App parameters
│   ├── 📋 abbreviations.json            # Azure resource naming abbreviations
│   └── 📂 modules/                      # Modular Bicep components
│       ├── ⚙️ network.bicep             # VNet, subnets, private DNS zones
│       ├── ⚙️ identity.bicep            # User-assigned managed identity
│       ├── ⚙️ storage.bicep             # Storage account + private endpoint
│       ├── ⚙️ cosmos.bicep              # Cosmos DB + private endpoint
│       ├── ⚙️ ai-services.bicep         # Azure AI Foundry account + project + model deployment + PE
│       ├── ⚙️ document-intelligence.bicep # Doc Intelligence + private endpoint
│       ├── ⚙️ key-vault.bicep           # Key Vault + private endpoint
│       ├── ⚙️ container-registry.bicep  # ACR for container images
│       ├── ⚙️ container-apps.bicep      # CAE + backend/frontend container apps
│       ├── ⚙️ role-assignments.bicep    # RBAC role assignments
│       ├── ⚙️ monitoring.bicep          # Application Insights + Log Analytics
│       └── ⚙️ event-processing.bicep    # Event Grid subscriptions
│
├── 📂 src/                              # 🚀 Core Application Source Code
│   ├── 📂 containerapp/                 # FastAPI Backend Service
│   │   ├── 🚀 main.py                   # FastAPI app lifecycle & configuration
│   │   ├── 🔌 api_routes.py             # HTTP endpoints & request handlers
│   │   ├── 🔧 dependencies.py           # Azure client initialization & management
│   │   ├── 📋 models.py                 # Pydantic data models & schemas
│   │   ├── ⚙️ blob_processing.py        # Document processing pipeline orchestration
│   │   ├── 🎛️ logic_app_manager.py     # Azure Logic Apps concurrency management
│   │   ├── 🐳 Dockerfile                # Container image definition
│   │   ├── 📦 pyproject.toml            # Python dependencies & project metadata (uv)
│   │   ├── 🔒 uv.lock                   # Pinned, reproducible dependency lockfile
│   │   ├── 📄 REFACTORING_SUMMARY.md    # Architecture documentation
│   │   │
│   │   ├── 📂 ai_ocr/                   # 🧠 AI Processing Engine
│   │   │   ├── 🔍 process.py            # Main processing orchestration & workflow
│   │   │   ├── 🔗 chains.py             # Microsoft Agent Framework extraction workflows
│   │   │   ├── 🤖 model.py              # Configuration models & data structures
│   │   │   ├── ⏱️ timeout.py            # Processing timeout management
│   │   │   │
│   │   │   ├── 📂 agents/               # 🤝 Microsoft Agent Framework integration
│   │   │   │   └── 🔌 client.py         # Foundry/OpenAI chat client + sync bridge
│   │   │   │
│   │   │   └── 📂 azure/                # ☁️ Azure Service Integrations
│   │   │       ├── ⚙️ config.py         # Environment & configuration management
│   │   │       ├── 📄 doc_intelligence.py # Azure Document Intelligence OCR
│   │   │       ├── 🖼️ images.py         # PDF to image conversion utilities
│   │   │       └── 🤖 openai_ops.py     # Azure OpenAI API operations
│   │   │
│   │   ├── 📂 example-datasets/         # 📊 Default Dataset Configurations
│   │   ├── 📂 datasets/                 # 📁 Runtime dataset storage
│   │   └── 📂 evaluators/               # 📈 Data quality evaluation modules
│   │
│   └── 📂 evaluators/                   # 🧪 Evaluation Framework
│       ├── 📋 field_evaluator_base.py   # Abstract base class for evaluators
│       ├── 🔤 fuzz_string_evaluator.py  # Fuzzy string matching evaluation
│       ├── 🎯 cosine_similarity_string_evaluator.py # Semantic similarity evaluation
│       ├── 🎛️ custom_string_evaluator.py # Custom evaluation logic
│       ├── 📊 json_evaluator.py         # JSON structure validation
│       └── 📂 tests/                    # Unit tests for evaluators
│
├── 📂 frontend-next/                    # 🖥️ Next.js Web Interface
│   ├── 📱 src/app/                      # App Router pages and API routes
│   │   ├── 📄 page.tsx                  # Home page with document processing
│   │   ├── 📂 explore/                  # Document browsing & analysis
│   │   ├── 📂 settings/                 # Configuration management
│   │   ├── 📂 instructions/             # Help & documentation
│   │   ├── 📂 api-docs/                 # API reference documentation
│   │   ├── 📂 mcp/                      # MCP integration info
│   │   └── 📂 api/                      # Backend proxy API routes
│   ├── 📂 src/components/               # Reusable React components
│   ├── 📂 src/lib/                      # API client & utilities
│   ├── 🐳 Dockerfile                    # Frontend container definition
│   ├── 📦 package.json                  # Node.js dependencies
│   └── ⚙️ next.config.js               # Next.js configuration
│
├── 📂 demo/                             # 📋 Sample Datasets & Examples
│   ├── 📂 default-dataset/              # General business documents dataset
│   │   ├── 📄 system_prompt.txt         # AI extraction instructions
│   │   ├── 📊 output_schema.json        # Expected data structure
│   │   ├── 📄 ground_truth.json         # Validation reference data
│   │   └── 📄 Invoice Sample.pdf        # Sample document for testing
│   │
│   └── 📂 medical-dataset/              # Healthcare documents dataset
│       ├── 📄 system_prompt.txt         # Medical-specific extraction rules
│       ├── 📊 output_schema.json        # Medical data structure
│       └── 📄 eyes_surgery_pre_1_4.pdf  # Sample medical document
│
├── 📂 notebooks/                        # 📈 Analytics & Evaluation Tools
│   ├── 🧪 evaluator.ipynb              # Comprehensive evaluation dashboard
│   ├── 📊 output.json                  # Evaluation results & metrics
│   ├── 📦 requirements.txt              # Jupyter notebook dependencies
│   ├── 📄 README.md                     # Notebook usage instructions
│   └── 📂 outputs/                      # Historical evaluation results
│
└── 📂 docs/                             # 📚 Documentation & Assets
    └── 🖼️ ArchitectureOverview.png      # System architecture diagram
```

### 🧪 Local Development Setup

```bash
# Setup development environment (uv manages the venv + Python 3.13)
cd src/containerapp
uv sync

# Configure local environment
cp ../../.env.template .env
# Edit .env with your development credentials

# Run with hot reload
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Access API documentation
open http://localhost:8000/docs
```

### 🔧 Key Technologies & Libraries

| Category | Technologies |
|----------|-------------|
| **🚀 API Framework** | FastAPI, Uvicorn, Pydantic |
| **🧠 AI/ML** | Microsoft Agent Framework, Azure AI Foundry, OpenAI SDK, Azure AI SDK |
| **☁️ Azure Services** | Azure SDK (Blob, Cosmos, Document Intelligence, Key Vault) |
| **📱 Frontend** | Next.js 15, React, Tailwind CSS, shadcn/ui |
| **📄 Document Processing** | PyMuPDF, Pillow, PyPDF2 |
| **📊 Data & Analytics** | Pandas, NumPy, Matplotlib |
| **🔒 Security** | Azure Identity, managed identities, Private Endpoints |

---

##  API Reference: Complete Documentation

### 🚀 Core Processing Endpoints

<details>
<summary><b>📄 POST /api/process-blob - Process Document from Storage</b></summary>

**Request**:
```json
{
  "blob_url": "https://storage.blob.core.windows.net/datasets/default-dataset/invoice.pdf",
  "dataset_name": "default-dataset",
  "priority": "normal",
  "webhook_url": "https://your-app.com/webhooks/argus",
  "metadata": {
    "source": "email_attachment",
    "user_id": "user123"
  }
}
```

**Response**:
```json
{
  "status": "success",
  "job_id": "job_12345",
  "extraction_results": {
    "invoice_number": "INV-2024-001",
    "total_amount": "$1,250.00",
    "confidence_score": 0.94
  },
  "processing_time": "2.3s",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

</details>

<details>
<summary><b>📤 POST /api/process-file - Direct File Upload</b></summary>

**Request** (multipart/form-data):
```
file: [PDF/Image file]
dataset_name: "default-dataset"
priority: "high"
```

**Response**:
```json
{
  "status": "success",
  "job_id": "job_12346",
  "blob_url": "https://storage.blob.core.windows.net/temp/uploaded_file.pdf",
  "extraction_results": {...},
  "processing_time": "1.8s"
}
```

</details>

<details>
<summary><b>💬 POST /api/chat - Interactive Document Q&A</b></summary>

**Request**:
```json
{
  "blob_url": "https://storage.blob.core.windows.net/datasets/contract.pdf",
  "question": "What are the payment terms and penalties for late payment?",
  "context": "focus on financial obligations",
  "temperature": 0.1
}
```

**Response**:
```json
{
  "answer": "Payment terms are Net 30 days. Late payment penalty is 1.5% per month on outstanding balance...",
  "confidence": 0.91,
  "sources": [
    {"page": 2, "section": "Payment Terms"},
    {"page": 5, "section": "Default Provisions"}
  ],
  "processing_time": "1.2s"
}
```

</details>

### ⚙️ Configuration Management

<details>
<summary><b>🔧 GET/POST /api/configuration - System Configuration</b></summary>

**GET Response**:
```json
{
  "openai_settings": {
    "endpoint": "https://your-openai.openai.azure.com/",
    "model": "gpt-5.4",
    "temperature": 0.1,
    "max_tokens": 4000
  },
  "processing_settings": {
    "max_concurrent_jobs": 5,
    "timeout_seconds": 300,
    "retry_attempts": 3
  },
  "datasets": ["default-dataset", "medical-dataset", "financial-reports"]
}
```

**POST Request**:
```json
{
  "openai_settings": {
    "temperature": 0.05,
    "max_tokens": 6000
  },
  "processing_settings": {
    "max_concurrent_jobs": 8
  }
}
```

</details>

### 📊 Monitoring & Analytics

<details>
<summary><b>📈 GET /api/metrics - Performance Metrics</b></summary>

**Response**:
```json
{
  "period": "last_24h",
  "summary": {
    "total_documents": 1247,
    "successful_extractions": 1198,
    "failed_extractions": 49,
    "success_rate": 96.1,
    "avg_processing_time": "2.3s"
  },
  "performance": {
    "p50_processing_time": "1.8s",
    "p95_processing_time": "4.2s",
    "p99_processing_time": "8.1s"
  },
  "errors": {
    "ocr_failures": 12,
    "ai_timeouts": 8,
    "storage_issues": 3,
    "other": 26
  }
}
```

</details>

---

##  Contributing & Community

### 🎯 How to Contribute

We welcome contributions! Here's how to get started:

1. **🍴 Fork & Clone**:
   ```bash
   git clone https://github.com/your-username/ARGUS.git
   cd ARGUS
   ```

2. **🌿 Create Feature Branch**:
   ```bash
   git checkout -b feature/amazing-improvement
   ```

3. **🧪 Develop & Test**:
   ```bash
   # Setup development environment
   ./scripts/setup-dev.sh

   # Run tests
   pytest tests/ -v

   # Lint code
   black src/ && flake8 src/
   ```

4. **📝 Document Changes**:
   ```bash
   # Update documentation
   # Add examples to README
   # Update API documentation
   ```

5. **🚀 Submit PR**:
   ```bash
   git commit -m "feat: add amazing improvement"
   git push origin feature/amazing-improvement
   # Create pull request on GitHub
   ```

### 📋 Contribution Guidelines

| Type | Guidelines |
|------|------------|
| **🐛 Bug Fixes** | Include reproduction steps, expected vs actual behavior |
| **✨ New Features** | Discuss in issues first, include tests and documentation |
| **📚 Documentation** | Clear examples, practical use cases, proper formatting |
| **🔧 Performance** | Benchmark results, before/after comparisons |

### 🏆 Recognition

Contributors will be recognized in:
- 📝 Release notes for significant contributions
- 🌟 Contributors section (with permission)
- 💬 Community showcase for innovative use cases

---

## 📞 Support & Resources

### 💬 Getting Help

| Resource | Description | Link |
|----------|-------------|------|
| **📚 Documentation** | Complete setup and usage guides | [docs/](docs/) |
| **📐 Architecture** | Solution flow & Azure architecture diagrams | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| **�🐛 Issue Tracker** | Bug reports and feature requests | [GitHub Issues](https://github.com/Azure-Samples/ARGUS/issues) |
| **💡 Discussions** | Community Q&A and ideas | [GitHub Discussions](https://github.com/Azure-Samples/ARGUS/discussions) |
| **📧 Team Contact** | Direct contact for enterprise needs | See team section below |

### 🔗 Additional Resources

- **📖 Azure Document Intelligence**: [Official Documentation](https://docs.microsoft.com/azure/applied-ai-services/form-recognizer/)
- **🤖 Azure AI Foundry**: [Service Documentation](https://learn.microsoft.com/azure/ai-foundry/)
- **⚡ FastAPI**: [Framework Documentation](https://fastapi.tiangolo.com/)
- **🤝 Microsoft Agent Framework**: [Documentation](https://learn.microsoft.com/agent-framework/)

---

## 👥 Team

- **Alberto Gallo**
- **Petteri Johansson**
- **Christin Pohl**
- **Konstantinos Mavrodis**

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

---

<div align="center">

## 🚀 Ready to Transform Your Document Processing?

**Deploy ARGUS in minutes and start extracting intelligence from your documents today!**

```bash
git clone https://github.com/Azure-Samples/ARGUS.git && cd ARGUS && azd up
```

<br>

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template)
[![Open in Dev Container](https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/Azure-Samples/ARGUS)

<br>

**⭐ Star this repo if ARGUS helps your document processing needs!**

</div>
