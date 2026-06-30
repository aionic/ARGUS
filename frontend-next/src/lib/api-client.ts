/**
 * Backend API Client for ARGUS Frontend
 * 
 * This client provides type-safe methods for interacting with the ARGUS backend API.
 */

// Document types
export type Tier = "economy" | "standard" | "premium"
export type CostStage = "ocr" | "extraction" | "evaluation" | "summary" | "content_understanding" | "paddle_pregate"
export type PricingSource = "azure_retail" | "fallback" | "mixed"
export type FlagStage = "preflight" | "quality"
export type ExtractionBackend =
  | "content_understanding"
  | "gpt"
  | "content_understanding+gpt"
  | "skipped_paddle_pregate"

export interface CostStageUsage {
  stage: CostStage
  model: string
  input_tokens: number
  output_tokens: number
  usd: number
}

export interface Cost {
  per_stage: CostStageUsage[]
  total_input_tokens: number
  total_output_tokens: number
  total_usd: number
  usd_per_page: number
  pricing_source: PricingSource
  model_breakdown: Record<string, number>
  // Pricing UX: list (undiscounted Azure) price + applied agreement discount.
  // Older documents may omit these — `total_usd` then equals list price.
  list_total_usd?: number
  list_usd_per_page?: number
  discount_pct?: number
  consumption_available?: boolean
}

export type CostObject = Cost
export type DocumentCost = Cost

export interface PricingSettings {
  discount_pct: number
  consumption_available: boolean
}

export interface EffectiveConfig {
  tier: Tier
  extraction_model: string
  enable_ocr: boolean
  enable_images: boolean
  enable_evaluation: boolean
  enable_summary: boolean
  summary_model: string
  use_rules_engine: boolean
  enable_preprocessing: boolean
}

export interface FlagEmail {
  sent_mock: boolean
  to: string
  subject: string
  body: string
  sent_at: string
  message_id: string
}

export interface Flag {
  flagged: boolean
  reasons: string[]
  stage: FlagStage
  flagged_at: string
  email?: FlagEmail
}

export type FlagObject = Flag
export type DocumentFlag = Flag

export interface FlaggedItem {
  id: string
  dataset: string
  filename: string
  reasons: string[]
  stage: FlagStage
  flagged_at: string
  email_sent?: boolean
}

export interface ProfilingTierReport {
  avg_usd_per_page: number
  avg_tokens: number
  success_rate: number
  failure_types: Record<string, number>
  quality_dist: Record<string, number>
}

export interface ProfilingReport {
  per_tier: Record<string, ProfilingTierReport>
}

export interface GenerateFlagEmailResponse {
  subject: string
  body: string
  to?: string
  from?: string
}

export interface SendFlagEmailMockPayload {
  document_id: string
  to: string
  subject: string
  body: string
}

export interface SendFlagEmailMockResponse {
  success: boolean
  message_id: string
  mock: boolean
}

export interface RunCostProfilingPayload {
  dataset?: string
  tiers: string[]
}

export interface DocumentState {
  file_landed?: boolean
  ocr_completed?: boolean
  gpt_extraction_completed?: boolean
  gpt_evaluation_completed?: boolean
  gpt_summary_completed?: boolean
  processing_completed?: boolean
  error?: boolean
}

export interface CuFallback {
  triggered: boolean
  reasons?: string[]
  cu_mean_confidence?: number | null
  cu_confidence_threshold?: number | null
  pre_fallback_flag_stage?: string | null
  triggered_at?: string
}

export interface DocumentProperties {
  blob_name?: string
  blob_size?: number
  request_timestamp?: string
  num_pages?: number
  dataset?: string
  total_time?: number
  total_time_seconds?: number
  cost?: Cost
  flag?: Flag
  extraction_backend_used?: ExtractionBackend
  cu_fallback?: CuFallback
}

export interface DocumentExtractedData {
  ocr_output?: string | Record<string, unknown>[]
  gpt_output?: Record<string, unknown>
  gpt_evaluation?: Record<string, unknown>
  gpt_summary?: string
  gpt_extraction_output?: Record<string, unknown>
}

export interface Document {
  id: string
  partitionKey?: string
  properties?: DocumentProperties
  state?: DocumentState
  extracted_data?: DocumentExtractedData
  errors?: string | string[]
  timestamp?: string
  // Additional fields that may be present at root level
  created_at?: string
  processing_time?: number
  num_pages?: number
  total_time_seconds?: number
  // OCR and summary data at root level
  ocr_text?: string
  summary?: string
  gpt_output?: Record<string, unknown>
  gpt_evaluation?: Record<string, unknown>
  // Model configuration
  model_input?: Record<string, unknown>
  processing_options?: Record<string, boolean>
}

export interface DocumentsResponse {
  documents: Document[]
  count: number
}

// Configuration types
export interface ProcessingOptions {
  include_ocr?: boolean
  include_images?: boolean
  enable_ocr?: boolean
  enable_images?: boolean
  enable_summary?: boolean
  enable_evaluation?: boolean
  extraction_backend?: string
  extraction_model?: string
  summary_model?: string
  tier?: Tier
  use_rules_engine?: boolean
  rules?: Record<string, unknown>
  enable_preprocessing?: boolean
  enable_enhancement?: boolean
  skip_if_still_bad?: boolean
  ocr_provider?: "azure" | "mistral"
}

export interface DatasetConfig {
  system_prompt?: string
  model_prompt?: string
  output_schema?: Record<string, unknown>
  example_schema?: Record<string, unknown>
  max_pages_per_chunk?: number
  tier?: Tier
  use_rules_engine?: boolean
  rules?: Record<string, unknown>
  effective_config?: EffectiveConfig
  processing_options?: ProcessingOptions
}

export interface Configuration {
  id?: string
  partitionKey?: string
  datasets: Record<string, DatasetConfig>
}

// Chat types
export interface ChatMessage {
  role: "user" | "assistant"
  content: string
}

export interface ChatRequest {
  document_id: string
  message: string
  chat_history?: ChatMessage[]
}

export interface ChatResponse {
  response: string
  finish_reason?: string
  usage?: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
}

// MCP types
export interface MCPTool {
  name: string
  description: string
}

export interface MCPInfo {
  name: string
  description: string
  version: string
  transport: string
  endpoints: {
    mcp: string
  }
  tools: MCPTool[]
  configuration_example: {
    mcpServers: {
      argus: {
        url: string
        headers?: Record<string, string>
      }
    }
  }
  auth_required?: boolean
}

// Settings types
export interface OpenAISettings {
  openai_endpoint?: string
  openai_key?: string
  deployment_name?: string
  ocr_provider?: string
  mistral_endpoint?: string
  mistral_key?: string
  mistral_model?: string
  note?: string
}

export interface ConcurrencySettings {
  enabled?: boolean
  current_max_runs?: number
  trigger_concurrency?: number
  workflow_name?: string
  subscription_id?: string
  resource_group?: string
  logic_app_name?: string
  error?: string
}

// Dataset types
export interface DatasetInfo {
  name: string
  system_prompt_preview?: string
  schema_fields?: string[]
  max_pages_per_chunk?: number
}

export interface DatasetsResponse {
  datasets: DatasetInfo[]
}

export interface CreateDatasetRequest {
  dataset_name: string
  system_prompt: string
  output_schema: Record<string, unknown>
  max_pages_per_chunk?: number
}

export interface CreateDatasetResponse {
  success: boolean
  dataset_name: string
  message: string
  configuration: {
    system_prompt_length: number
    output_schema_fields: string[]
    max_pages_per_chunk: number
  }
}

// Health check types
export interface HealthResponse {
  status: string
  timestamp?: string
  services?: {
    storage?: string
    cosmos_db?: string
  }
}

// Upload types
export interface UploadUrlResponse {
  upload_url: string
  method: string
  headers: Record<string, string>
  filename: string
  dataset: string
  blob_path: string
  expires_in: string
  instructions: string[]
}

// Correction types
export interface Correction {
  id?: string
  timestamp: string
  user_id: string
  notes: string
  corrected_data: Record<string, unknown>
  original_data?: Record<string, unknown>
}

export interface CorrectionHistoryResponse {
  corrections: Correction[]
}

// Upload options
export interface UploadOptions {
  run_ocr?: boolean
  run_gpt_vision?: boolean
  run_summary?: boolean
  run_evaluation?: boolean
}

/**
 * Backend API Client
 * 
 * Provides methods for interacting with the ARGUS backend API.
 */
class BackendClient {
  private baseUrl: string = ''
  private initialized: boolean = false
  private initPromise: Promise<void> | null = null

  constructor() {
    // All requests are proxied through Next.js API routes to keep the API key server-side
  }

  /**
   * Initialize the client by fetching the backend URL
   */
  private async initialize(): Promise<void> {
    if (this.initialized) {
      return
    }

    // Avoid multiple concurrent initializations
    if (this.initPromise) {
      return this.initPromise
    }

    this.initPromise = (async () => {
      // Route all requests through the Next.js proxy to keep the API key server-side
      this.baseUrl = '/api/backend'
      this.initialized = true
    })()

    return this.initPromise
  }

  /**
   * Get the backend base URL, initializing if needed
   */
  async getBackendBaseUrl(): Promise<string> {
    await this.initialize()
    return this.baseUrl
  }

  private async fetch<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    await this.initialize()
    const url = `${this.baseUrl}${endpoint}`
    
    const response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `HTTP error ${response.status}`)
    }

    return response.json()
  }

  // Health endpoints
  async healthCheck(): Promise<HealthResponse> {
    return this.fetch<HealthResponse>("/health")
  }

  // Document endpoints
  async listDocuments(dataset?: string, limit?: number): Promise<DocumentsResponse> {
    const params = new URLSearchParams()
    if (dataset) params.append("dataset", dataset)
    if (limit) params.append("limit", limit.toString())
    
    const query = params.toString() ? `?${params.toString()}` : ""
    return this.fetch<DocumentsResponse>(`/api/documents${query}`)
  }

  // Alias for listDocuments for backward compatibility
  async getDocuments(dataset?: string, limit?: number): Promise<DocumentsResponse> {
    return this.listDocuments(dataset, limit)
  }

  async getDocument(documentId: string): Promise<Document> {
    return this.fetch<Document>(`/api/documents/${encodeURIComponent(documentId)}`)
  }

  async deleteDocument(documentId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(
      `/api/documents/${encodeURIComponent(documentId)}`,
      { method: "DELETE" }
    )
  }

  async reprocessDocument(documentId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(
      `/api/documents/${encodeURIComponent(documentId)}/reprocess`,
      { method: "POST" }
    )
  }

  async getFlaggedDocuments(): Promise<FlaggedItem[]> {
    return this.fetch<FlaggedItem[]>("/api/documents/flagged")
  }

  async generateFlagEmail(
    documentId: string,
    promptTemplate?: string
  ): Promise<GenerateFlagEmailResponse> {
    return this.fetch<GenerateFlagEmailResponse>(
      `/api/documents/${encodeURIComponent(documentId)}/flag-email/generate`,
      {
        method: "POST",
        body: JSON.stringify(promptTemplate ? { prompt_template: promptTemplate } : {}),
      }
    )
  }

  async sendFlagEmailMock(
    payload: SendFlagEmailMockPayload
  ): Promise<SendFlagEmailMockResponse> {
    return this.fetch<SendFlagEmailMockResponse>("/api/flag-email/send", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  }

  async runCostProfiling(payload: RunCostProfilingPayload): Promise<ProfilingReport> {
    return this.fetch<ProfilingReport>("/api/profiling/run", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  }

  async getDocumentFileUrl(documentId: string): Promise<string> {
    // The backend serves the file directly at /file endpoint
    // Return the URL for iframe/embed use
    await this.initialize()
    return `${this.baseUrl}/api/documents/${encodeURIComponent(documentId)}/file`
  }

  // Configuration endpoints
  async getConfiguration(): Promise<Configuration> {
    return this.fetch<Configuration>("/api/configuration")
  }

  async updateConfiguration(config: Record<string, unknown>): Promise<{ status: string; message: string }> {
    return this.fetch<{ status: string; message: string }>("/api/configuration", {
      method: "POST",
      body: JSON.stringify(config),
    })
  }

  async refreshConfiguration(): Promise<{ status: string; message: string }> {
    return this.fetch<{ status: string; message: string }>("/api/configuration/refresh", {
      method: "POST",
    })
  }

  // Dataset endpoints
  async getDatasets(): Promise<DatasetsResponse> {
    return this.fetch<DatasetsResponse>("/api/datasets")
  }

  async createDataset(request: CreateDatasetRequest): Promise<CreateDatasetResponse> {
    return this.fetch<CreateDatasetResponse>("/api/datasets", {
      method: "POST",
      body: JSON.stringify(request),
    })
  }

  async getDatasetDocuments(datasetName: string): Promise<DocumentsResponse> {
    return this.fetch<DocumentsResponse>(
      `/api/datasets/${encodeURIComponent(datasetName)}/documents`
    )
  }

  // Chat endpoints
  async chat(request: ChatRequest): Promise<ChatResponse> {
    return this.fetch<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify(request),
    })
  }

  async sendChatMessage(documentId: string, message: string, chatHistory?: ChatMessage[]): Promise<ChatResponse> {
    return this.chat({
      document_id: documentId,
      message,
      chat_history: chatHistory,
    })
  }

  // MCP endpoints
  async getMCPInfo(): Promise<MCPInfo> {
    return this.fetch<MCPInfo>("/mcp/info")
  }

  // Settings endpoints
  async getOpenAISettings(): Promise<OpenAISettings> {
    return this.fetch<OpenAISettings>("/api/openai-settings")
  }

  async updateOpenAISettings(settings: Partial<OpenAISettings>): Promise<{ message: string }> {
    return this.fetch<{ message: string }>("/api/openai-settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    })
  }

  async getConcurrencySettings(): Promise<ConcurrencySettings> {
    return this.fetch<ConcurrencySettings>("/api/concurrency")
  }

  async updateConcurrencySettings(settings: { max_runs: number }): Promise<{ success: boolean; message: string }> {
    return this.fetch<{ success: boolean; message: string }>("/api/concurrency", {
      method: "PUT",
      body: JSON.stringify(settings),
    })
  }

  async getConcurrencyDiagnostics(): Promise<Record<string, unknown>> {
    return this.fetch<Record<string, unknown>>("/api/concurrency/diagnostics")
  }

  async getPricingSettings(): Promise<PricingSettings> {
    return this.fetch<PricingSettings>("/api/pricing-settings")
  }

  async updatePricingSettings(
    settings: Partial<PricingSettings>
  ): Promise<{ message: string; pricing: PricingSettings }> {
    return this.fetch<{ message: string; pricing: PricingSettings }>("/api/pricing-settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    })
  }

  // Upload endpoints
  async getUploadUrl(filename: string, dataset: string = "default-dataset"): Promise<UploadUrlResponse> {
    const params = new URLSearchParams({ filename, dataset })
    return this.fetch<UploadUrlResponse>(`/api/upload-url?${params.toString()}`)
  }

  async processBlob(blobUrl: string, dataset?: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>("/api/process-blob", {
      method: "POST",
      body: JSON.stringify({ blob_url: blobUrl, dataset }),
    })
  }

  // File upload with options - proxies through backend to blob storage
  async uploadFile(
    datasetName: string,
    file: File,
    options?: UploadOptions
  ): Promise<{ message: string; id: string }> {
    await this.initialize()

    const formData = new FormData()
    formData.append('file', file)

    // Build query params for processing options
    const params = new URLSearchParams()
    if (options) {
      if (options.run_ocr !== undefined) params.set('run_ocr', String(options.run_ocr))
      if (options.run_gpt_vision !== undefined) params.set('run_gpt_vision', String(options.run_gpt_vision))
      if (options.run_summary !== undefined) params.set('run_summary', String(options.run_summary))
      if (options.run_evaluation !== undefined) params.set('run_evaluation', String(options.run_evaluation))
    }

    const queryString = params.toString()
    const url = `${this.baseUrl}/api/datasets/${encodeURIComponent(datasetName)}/upload${queryString ? `?${queryString}` : ''}`

    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `Upload failed: ${response.status}`)
    }

    const result = await response.json()
    return {
      message: result.message || `File ${file.name} uploaded successfully to ${datasetName}. Processing will begin automatically.`,
      id: result.document_id || result.blob_path || '',
    }
  }

  // Correction endpoints
  async getCorrectionHistory(documentId: string): Promise<CorrectionHistoryResponse> {
    return this.fetch<CorrectionHistoryResponse>(
      `/api/documents/${encodeURIComponent(documentId)}/corrections`
    )
  }

  async submitCorrection(
    documentId: string,
    correctedData: Record<string, unknown>,
    notes: string,
    userId: string = "anonymous"
  ): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(
      `/api/documents/${encodeURIComponent(documentId)}/corrections`,
      {
        method: "PATCH",
        body: JSON.stringify({
          corrected_data: correctedData,
          notes,
          user_id: userId,
        }),
      }
    )
  }

  // Statistics (local development)
  async getStats(): Promise<{
    total_documents: number
    completed_documents: number
    pending_documents: number
    success_rate: number
  }> {
    return this.fetch(`/api/stats`)
  }
}

// Export singleton instance
export const backendClient = new BackendClient()

export function getFlaggedDocuments(): Promise<FlaggedItem[]> {
  return backendClient.getFlaggedDocuments()
}

export function generateFlagEmail(
  documentId: string,
  promptTemplate?: string
): Promise<GenerateFlagEmailResponse> {
  return backendClient.generateFlagEmail(documentId, promptTemplate)
}

export function sendFlagEmailMock(
  payload: SendFlagEmailMockPayload
): Promise<SendFlagEmailMockResponse> {
  return backendClient.sendFlagEmailMock(payload)
}

export function runCostProfiling(payload: RunCostProfilingPayload): Promise<ProfilingReport> {
  return backendClient.runCostProfiling(payload)
}

// Export the class for testing
export { BackendClient }
