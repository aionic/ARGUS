"use client"

import * as React from "react"
import { motion, AnimatePresence } from "framer-motion"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  X,
  Download,
  RotateCcw,
  Trash2,
  FileText,
  Image,
  CheckCircle,
  XCircle,
  Clock,
  Send,
  MessageSquare,
  History,
  Loader2,
  Code,
  FileJson,
  Brain,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Copy,
  Pencil,
  Save,
  RefreshCw,
  Settings,
  DollarSign,
  Gauge,
  ZoomIn,
  ZoomOut,
  Move,
  RotateCw
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { backendClient, type Document, type Cost, type ExtractionBackend, type CuChunkEvidence, type CuFallback, type CuUsage, type OcrConfidence, type PageMetric } from "@/lib/api-client"
import { formatDate, formatDuration, formatBytes } from "@/lib/utils"

interface ProcessedDocument {
  id: string
  dataset: string
  fileName: string
  status: "completed" | "processing" | "failed"
  fileLanded: boolean
  ocrCompleted: boolean
  gptExtraction: boolean
  gptEvaluation: boolean
  gptSummary: boolean
  finished: boolean
  errors?: string
  timestamp: Date
  totalTime?: number
  pages?: number
  size?: number
  cost?: Cost
  tier?: string
  flagged?: boolean
  selected: boolean
}

interface DocumentDetailSheetProps {
  document: ProcessedDocument | null
  onClose: () => void
  onReprocess: (doc: ProcessedDocument) => void
  onDelete: (doc: ProcessedDocument) => void
  onRefresh?: () => void
  /** Bumped by the parent (via SSE) to trigger a live refresh of the open doc. */
  refreshSignal?: number
}

interface ChatMessage {
  role: "user" | "assistant"
  content: string
}

interface Correction {
  timestamp: string
  corrector_id?: string
  notes?: string
  original_data?: Record<string, unknown> | null
  corrected_data?: Record<string, unknown>
  correction_number?: number
}

function formatUsd(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "N/A"
  const absValue = Math.abs(value)
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: absValue > 0 && absValue < 1 ? 4 : 2,
  }).format(value)
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "N/A"
  return new Intl.NumberFormat("en-US").format(value)
}

function formatStageName(stage: string): string {
  return stage.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
}

/** Confidence band → semantic color classes for the field-confidence view. */
function confidenceColor(score: number): { text: string; bar: string; label: string } {
  if (score >= 0.8) return { text: "text-green-600 dark:text-green-400", bar: "bg-green-500", label: "High" }
  if (score >= 0.6) return { text: "text-amber-600 dark:text-amber-400", bar: "bg-amber-500", label: "Medium" }
  return { text: "text-red-600 dark:text-red-400", bar: "bg-red-500", label: "Low" }
}

/** Flatten Content Understanding per-chunk confidence into one {path: score} map,
 *  keeping the lowest score on collision so the worst case surfaces. */
function flattenCuConfidence(
  cuConfidence: Record<string, Record<string, number>> | undefined
): Record<string, number> {
  const merged: Record<string, number> = {}
  if (!cuConfidence) return merged
  for (const chunk of Object.values(cuConfidence)) {
    if (!chunk || typeof chunk !== "object") continue
    for (const [path, score] of Object.entries(chunk)) {
      if (typeof score === "number" && Number.isFinite(score)) {
        merged[path] = path in merged ? Math.min(merged[path], score) : score
      }
    }
  }
  return merged
}

/** Best-effort resolution of a dotted/bracketed field path (e.g. "line_items[0].total")
 *  against the extracted data object. Returns a short display string. */
function resolveByPath(data: unknown, path: string): string {
  if (data == null) return ""
  const tokens = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean)
  let current: unknown = data
  for (const token of tokens) {
    if (current == null || typeof current !== "object") return ""
    current = (current as Record<string, unknown>)[token]
  }
  if (current == null) return ""
  if (typeof current === "object") return Array.isArray(current) ? `[${current.length} items]` : "{…}"
  return String(current)
}

export function DocumentDetailSheet({
  document,
  onClose,
  onReprocess,
  onDelete,
  onRefresh,
  refreshSignal,
}: DocumentDetailSheetProps) {
  const [fullDocument, setFullDocument] = React.useState<Document | null>(null)
  const [isLoading, setIsLoading] = React.useState(false)
  const [isRefreshing, setIsRefreshing] = React.useState(false)
  const [activeTab, setActiveTab] = React.useState("extracted")

  // Chat state
  const [chatMessages, setChatMessages] = React.useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = React.useState("")
  const [isSending, setIsSending] = React.useState(false)

  // Corrections state
  const [corrections, setCorrections] = React.useState<Correction[]>([])

  // Edit mode state for extraction
  const [isEditMode, setIsEditMode] = React.useState(false)
  const [editedJson, setEditedJson] = React.useState("")
  const [editNotes, setEditNotes] = React.useState("")
  const [isSavingEdit, setIsSavingEdit] = React.useState(false)

  // Helper to check for actual errors (not empty string or empty array)
  // ProcessedDocument.errors is already normalized to string by explore page
  const hasActualErrors = React.useMemo((): boolean => {
    const errors = document?.errors
    if (!errors) return false
    return errors.trim().length > 0 && errors.trim() !== '[]'
  }, [document?.errors])

  // Get error message as string
  const errorMessage = document?.errors || ''

  // PDF viewer state
  const [currentPage, setCurrentPage] = React.useState(1)

  // State for file URL
  const [fileUrl, setFileUrl] = React.useState<string | undefined>(undefined)

  // Image viewer state (zoom, pan, rotation)
  const [imageZoom, setImageZoom] = React.useState(1)
  const [imagePosition, setImagePosition] = React.useState({ x: 0, y: 0 })
  const [imageRotation, setImageRotation] = React.useState(0)
  const [isDragging, setIsDragging] = React.useState(false)
  const [dragStart, setDragStart] = React.useState({ x: 0, y: 0 })
  const imageContainerRef = React.useRef<HTMLDivElement>(null)

  // Check if file is an image
  const isImageFile = React.useMemo(() => {
    const fileName = document?.fileName?.toLowerCase() || ''
    return fileName.endsWith('.jpg') || fileName.endsWith('.jpeg') ||
           fileName.endsWith('.png') || fileName.endsWith('.gif') ||
           fileName.endsWith('.webp') || fileName.endsWith('.bmp')
  }, [document?.fileName])

  // Reset image viewer state when document changes
  React.useEffect(() => {
    setImageZoom(1)
    setImagePosition({ x: 0, y: 0 })
    setImageRotation(0)
  }, [document?.id])

  // Image zoom handlers
  const handleZoomIn = () => setImageZoom(prev => Math.min(prev + 0.25, 5))
  const handleZoomOut = () => setImageZoom(prev => Math.max(prev - 0.25, 0.25))
  const handleResetView = () => {
    setImageZoom(1)
    setImagePosition({ x: 0, y: 0 })
    setImageRotation(0)
  }
  const handleRotate = () => setImageRotation(prev => (prev + 90) % 360)

  // Mouse handlers for panning
  const handleMouseDown = (e: React.MouseEvent) => {
    if (imageZoom > 1) {
      setIsDragging(true)
      setDragStart({ x: e.clientX - imagePosition.x, y: e.clientY - imagePosition.y })
    }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging && imageZoom > 1) {
      setImagePosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y
      })
    }
  }

  const handleMouseUp = () => setIsDragging(false)
  const handleMouseLeave = () => setIsDragging(false)

  // Wheel zoom handler
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -0.1 : 0.1
    setImageZoom(prev => Math.min(Math.max(prev + delta, 0.25), 5))
  }

  // Load full document data
  React.useEffect(() => {
    if (document) {
      loadFullDocument()
      loadCorrections()
      setChatMessages([])
      setChatInput("")
      setCurrentPage(1)
    }
  }, [document?.id])

  // Get file URL when document changes
  React.useEffect(() => {
    async function loadFileUrl() {
      if (document?.id) {
        try {
          const url = await backendClient.getDocumentFileUrl(document.id)
          setFileUrl(url)
        } catch (error) {
          console.error("Failed to get file URL:", error)
        }
      }
    }
    loadFileUrl()
  }, [document?.id])

  // Live refresh: when the parent bumps refreshSignal (via SSE) for the open doc,
  // silently reload its full data + corrections. Skips the initial mount value.
  const lastRefreshSignalRef = React.useRef<number | undefined>(refreshSignal)
  React.useEffect(() => {
    if (refreshSignal === undefined) return
    if (refreshSignal === lastRefreshSignalRef.current) return
    lastRefreshSignalRef.current = refreshSignal
    if (document?.id) {
      loadFullDocument()
      loadCorrections()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal])

  async function loadFullDocument() {
    if (!document) return
    setIsLoading(true)
    try {
      const doc = await backendClient.getDocument(document.id)
      setFullDocument(doc)
    } catch (error) {
      console.error("Failed to load document:", error)
      toast.error("Failed to load document details")
    } finally {
      setIsLoading(false)
    }
  }

  async function loadCorrections() {
    if (!document) return
    try {
      console.log("Loading corrections for document ID:", document.id)
      const history = await backendClient.getCorrectionHistory(document.id)
      console.log("Corrections response:", history)
      setCorrections(history.corrections || [])
    } catch (error) {
      console.error("Failed to load corrections:", error)
    }
  }

  // Chat functions
  async function sendMessage() {
    if (!chatInput.trim() || !document) return

    const userMessage = chatInput.trim()
    setChatMessages((prev) => [...prev, { role: "user", content: userMessage }])
    setChatInput("")
    setIsSending(true)

    try {
      const response = await backendClient.sendChatMessage(document.id, userMessage)
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: response.response || "No response" },
      ])
    } catch (error) {
      console.error("Chat error:", error)
      toast.error("Failed to send message")
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Error: Failed to get response" },
      ])
    } finally {
      setIsSending(false)
    }
  }

  // Copy to clipboard
  function copyToClipboard(text: string) {
    navigator.clipboard.writeText(text)
    toast.success("Copied to clipboard")
  }

  // Download JSON
  function downloadJson(data: unknown, filename: string) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = window.document.createElement("a")
    a.href = url
    a.download = filename
    window.document.body.appendChild(a)
    a.click()
    window.document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  // Parse extracted_data from the document structure - MUST be before early return
  const rawExtractedData = fullDocument?.extracted_data as Record<string, unknown> | undefined

  // Get specific extraction outputs - handle both object and string formats
  const gptExtractionOutput = rawExtractedData?.gpt_extraction_output
  const gptExtractionWithEval = rawExtractedData?.gpt_extraction_output_with_evaluation
  const gptSummaryOutput = rawExtractedData?.gpt_summary_output as string | undefined
  const ocrOutput = rawExtractedData?.ocr_output

  // Get processing state and properties for timing info
  const processingState = fullDocument?.state as Record<string, unknown> | undefined
  const processingProperties = fullDocument?.properties as Record<string, unknown> | undefined
  const typedProperties = fullDocument?.properties as (NonNullable<Document["properties"]> & { tier?: string }) | undefined
  const processingOptions = fullDocument?.processing_options as Record<string, unknown> | undefined
  const documentCost = typedProperties?.cost ?? document?.cost
  const extractionBackendUsed = typedProperties?.extraction_backend_used
  const cuFallback = typedProperties?.cu_fallback
  const cuUsage = typedProperties?.content_understanding_usage
  const pageMetrics = typedProperties?.page_metrics ?? []
  const cuChunks = typedProperties?.content_understanding_chunks ?? []
  const tierUsed = typedProperties?.tier
    ?? (typeof processingOptions?.tier === "string" ? processingOptions.tier : undefined)
    ?? document?.tier

  // Parse JSON strings if needed - useMemo must be called unconditionally
  const extractedData = React.useMemo(() => {
    if (gptExtractionOutput) {
      // If it's already an object, return it directly
      if (typeof gptExtractionOutput === 'object') {
        return gptExtractionOutput
      }
      // If it's a string, try to parse it
      try {
        return JSON.parse(gptExtractionOutput as string)
      } catch {
        return gptExtractionOutput
      }
    }
    return rawExtractedData
  }, [gptExtractionOutput, rawExtractedData])

  const evaluationData = React.useMemo(() => {
    if (gptExtractionWithEval) {
      // If it's already an object, return it directly
      if (typeof gptExtractionWithEval === 'object') {
        return gptExtractionWithEval
      }
      // If it's a string, try to parse it
      try {
        return JSON.parse(gptExtractionWithEval as string)
      } catch {
        return gptExtractionWithEval
      }
    }
    return null
  }, [gptExtractionWithEval])

  // Field-level extraction confidence. Prefer the server-merged flat map; fall
  // back to flattening Content Understanding per-chunk confidence client-side.
  const fieldConfidence = React.useMemo<Record<string, number>>(() => {
    const fromBackend = fullDocument?.field_confidence
    if (fromBackend && Object.keys(fromBackend).length > 0) return fromBackend
    return flattenCuConfidence(typedProperties?.content_understanding_confidence)
  }, [fullDocument?.field_confidence, typedProperties?.content_understanding_confidence])

  const confidenceRows = React.useMemo(() => {
    return Object.entries(fieldConfidence)
      .map(([path, score]) => ({ path, score, value: resolveByPath(extractedData, path) }))
      .sort((a, b) => a.score - b.score)
  }, [fieldConfidence, extractedData])

  const confidenceMean = React.useMemo(() => {
    const scores = Object.values(fieldConfidence)
    if (scores.length === 0) return null
    return scores.reduce((sum, s) => sum + s, 0) / scores.length
  }, [fieldConfidence])

  const ocrConfidence: OcrConfidence | null | undefined = fullDocument?.ocr_confidence

  // Combine OCR text - handle both string and array formats
  const ocrText = React.useMemo(() => {
    if (ocrOutput) {
      // If it's already a string, return it directly
      if (typeof ocrOutput === 'string') {
        return ocrOutput
      }
      // If it's an array of page objects, combine them
      if (Array.isArray(ocrOutput)) {
        return ocrOutput.map((page: { page_number?: number; page_text?: string }) =>
          `--- Page ${(page.page_number || 0) + 1} ---\n${page.page_text || ''}`
        ).join('\n\n')
      }
    }
    return fullDocument?.ocr_text as string | undefined
  }, [ocrOutput, fullDocument?.ocr_text])

  const summaryData = gptSummaryOutput || fullDocument?.summary as string | undefined

  // Early return AFTER all hooks
  if (!document) return null

  return (
    <AnimatePresence>
      {document && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 z-40"
            onClick={onClose}
          />

          {/* Sheet */}
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed right-0 top-0 h-full w-[95vw] bg-background border-l z-50 flex flex-col"
          >
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b">
              <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" onClick={onClose}>
                  <X className="h-5 w-5" />
                </Button>
                <div>
                  <h2 className="font-semibold truncate max-w-[400px]">
                    {document.fileName}
                  </h2>
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Badge variant="secondary">{document.dataset}</Badge>
                    <span>•</span>
                    {document.status === "completed" && (
                      <Badge variant="default" className="bg-green-500 hover:bg-green-500">
                        <CheckCircle className="h-3 w-3 mr-1" />
                        Completed
                      </Badge>
                    )}
                    {document.status === "processing" && (
                      <Badge variant="secondary" className="bg-yellow-500/20 text-yellow-600 hover:bg-yellow-500/20">
                        <Clock className="h-3 w-3 mr-1" />
                        Processing
                      </Badge>
                    )}
                    {document.status === "failed" && (
                      <Badge variant="destructive">
                        <XCircle className="h-3 w-3 mr-1" />
                        Failed
                      </Badge>
                    )}
                    <span>•</span>
                    <span>{formatDate(document.timestamp)}</span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={async () => {
                    setIsRefreshing(true)
                    await loadFullDocument()
                    await loadCorrections()
                    // Notify parent to refresh document list for status updates
                    onRefresh?.()
                    setIsRefreshing(false)
                  }}
                  disabled={isRefreshing}
                >
                  <RefreshCw className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`} />
                  Refresh
                </Button>
                <Button variant="outline" size="sm" onClick={() => onReprocess(document)}>
                  <RotateCcw className="h-4 w-4 mr-2" />
                  Reprocess
                </Button>
                <Button variant="destructive" size="sm" onClick={() => onDelete(document)}>
                  <Trash2 className="h-4 w-4 mr-2" />
                  Delete
                </Button>
              </div>
            </div>

            {/* Processing Status */}
            <div className="px-6 py-4 border-b bg-gradient-to-r from-muted/50 to-muted/20">
              <div className="flex items-center justify-between gap-6">
                <ProcessingStep
                  label="File Landed"
                  completed={processingState?.file_landed as boolean ?? document.fileLanded}
                  hasError={hasActualErrors && !document.fileLanded}
                />
                <div className="h-[2px] flex-1 bg-border" />
                <ProcessingStep
                  label="OCR"
                  completed={processingState?.ocr_completed as boolean ?? document.ocrCompleted}
                  hasError={hasActualErrors && document.fileLanded && !document.ocrCompleted}
                />
                <div className="h-[2px] flex-1 bg-border" />
                <ProcessingStep
                  label="Extraction"
                  completed={processingState?.gpt_extraction_completed as boolean ?? document.gptExtraction}
                  hasError={hasActualErrors && document.ocrCompleted && !document.gptExtraction}
                />
                <div className="h-[2px] flex-1 bg-border" />
                <ProcessingStep
                  label="Evaluation"
                  completed={processingState?.gpt_evaluation_completed as boolean ?? document.gptEvaluation}
                  hasError={hasActualErrors && document.gptExtraction && !document.gptEvaluation}
                />
                <div className="h-[2px] flex-1 bg-border" />
                <ProcessingStep
                  label="Summary"
                  completed={processingState?.gpt_summary_completed as boolean ?? document.gptSummary}
                  hasError={hasActualErrors && document.gptEvaluation && !document.gptSummary}
                />
              </div>
              {hasActualErrors && (
                <div className="mt-4 p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive text-sm">
                  <strong>Error:</strong> {errorMessage}
                </div>
              )}
            </div>

            {/* Main Content - PDF Left, Tabs Right */}
            <div className="flex-1 flex overflow-hidden h-[calc(100vh-280px)]">
              {/* PDF Preview - Always Visible */}
              <div className="w-1/2 p-4 border-r">
                <Card className="flex flex-col h-full overflow-hidden">
                  <CardHeader className="pb-3 flex-shrink-0">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-lg">Document Preview</CardTitle>
                      <div className="flex items-center gap-2">
                        {isImageFile && fileUrl && (
                          <>
                            <Button variant="outline" size="icon" onClick={handleZoomOut} title="Zoom Out">
                              <ZoomOut className="h-4 w-4" />
                            </Button>
                            <span className="text-sm text-muted-foreground min-w-[3rem] text-center">
                              {Math.round(imageZoom * 100)}%
                            </span>
                            <Button variant="outline" size="icon" onClick={handleZoomIn} title="Zoom In">
                              <ZoomIn className="h-4 w-4" />
                            </Button>
                            <Button variant="outline" size="icon" onClick={handleRotate} title="Rotate">
                              <RotateCw className="h-4 w-4" />
                            </Button>
                            <Button variant="outline" size="icon" onClick={handleResetView} title="Reset View">
                              <RotateCcw className="h-4 w-4" />
                            </Button>
                          </>
                        )}
                        {fileUrl && (
                          <Button variant="outline" size="sm" asChild>
                            <a href={fileUrl} target="_blank" rel="noopener noreferrer">
                              <ExternalLink className="h-4 w-4 mr-2" />
                              Open
                            </a>
                          </Button>
                        )}
                      </div>
                    </div>
                    {isImageFile && imageZoom > 1 && (
                      <p className="text-xs text-muted-foreground mt-1">
                        <Move className="h-3 w-3 inline mr-1" />
                        Drag to pan • Scroll to zoom
                      </p>
                    )}
                  </CardHeader>
                  <CardContent className="flex-1 overflow-hidden p-4">
                    {fileUrl ? (
                      isImageFile ? (
                        <div
                          ref={imageContainerRef}
                          className="w-full h-full rounded-lg border overflow-hidden bg-muted/30 relative"
                          style={{ cursor: imageZoom > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default' }}
                          onMouseDown={handleMouseDown}
                          onMouseMove={handleMouseMove}
                          onMouseUp={handleMouseUp}
                          onMouseLeave={handleMouseLeave}
                          onWheel={handleWheel}
                        >
                          <img
                            src={fileUrl}
                            alt="Document Preview"
                            className="absolute top-1/2 left-1/2 max-w-none select-none"
                            style={{
                              transform: `translate(-50%, -50%) translate(${imagePosition.x}px, ${imagePosition.y}px) scale(${imageZoom}) rotate(${imageRotation}deg)`,
                              transformOrigin: 'center center',
                              transition: isDragging ? 'none' : 'transform 0.1s ease-out'
                            }}
                            draggable={false}
                          />
                        </div>
                      ) : (
                        <iframe
                          src={`${fileUrl}#page=${currentPage}`}
                          className="w-full h-full rounded-lg border"
                          title="Document Preview"
                        />
                      )
                    ) : (
                      <div className="h-full flex items-center justify-center text-muted-foreground">
                        <FileText className="h-12 w-12 opacity-50" />
                        <span className="ml-2">No preview available</span>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* Tabs - Right Side */}
              <Tabs value={activeTab} onValueChange={setActiveTab} className="w-1/2 flex flex-col overflow-hidden">
                <TabsList className="mx-4 mt-4 flex-wrap h-auto gap-1 flex-shrink-0">
                  <TabsTrigger value="extracted" className="flex items-center gap-2">
                    <FileJson className="h-4 w-4" />
                    Extraction
                  </TabsTrigger>
                  <TabsTrigger value="evaluation" className="flex items-center gap-2">
                    <CheckCircle className="h-4 w-4" />
                    Evaluation
                  </TabsTrigger>
                  <TabsTrigger value="confidence" className="flex items-center gap-2">
                    <Gauge className="h-4 w-4" />
                    Confidence
                  </TabsTrigger>
                  <TabsTrigger value="pages" className="flex items-center gap-2">
                    <FileText className="h-4 w-4" />
                    Pages
                  </TabsTrigger>
                  <TabsTrigger value="ocr" className="flex items-center gap-2">
                    <Code className="h-4 w-4" />
                    OCR
                  </TabsTrigger>
                  <TabsTrigger value="summary" className="flex items-center gap-2">
                    <Brain className="h-4 w-4" />
                    Summary
                  </TabsTrigger>
                  <TabsTrigger value="details" className="flex items-center gap-2">
                    <Clock className="h-4 w-4" />
                    Details
                  </TabsTrigger>
                  <TabsTrigger value="cost" className="flex items-center gap-2">
                    <DollarSign className="h-4 w-4" />
                    Cost
                  </TabsTrigger>
                  <TabsTrigger value="chat" className="flex items-center gap-2">
                    <MessageSquare className="h-4 w-4" />
                    Chat
                  </TabsTrigger>
                  <TabsTrigger value="corrections" className="flex items-center gap-2">
                    <History className="h-4 w-4" />
                    Corrections
                  </TabsTrigger>
                </TabsList>

                <div className="flex-1 overflow-hidden relative">
                  {isLoading ? (
                    <div className="p-4 space-y-4">
                      <Skeleton className="h-32 w-full" />
                      <Skeleton className="h-32 w-full" />
                    </div>
                  ) : (
                    <>
                      {/* Extraction Tab */}
                      <TabsContent value="extracted" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                        <Card className="flex flex-col h-full overflow-hidden">
                          <CardHeader className="pb-3 flex-shrink-0">
                            <div className="flex items-center justify-between">
                              <CardTitle className="text-lg">Extracted Data</CardTitle>
                              <div className="flex gap-2">
                                {!isEditMode ? (
                                  <>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      onClick={() => {
                                        setEditedJson(JSON.stringify(extractedData, null, 2))
                                        setEditNotes("")
                                        setIsEditMode(true)
                                      }}
                                    >
                                      <Pencil className="h-4 w-4 mr-2" />
                                      Edit
                                    </Button>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      onClick={() => copyToClipboard(JSON.stringify(extractedData, null, 2))}
                                    >
                                      <Copy className="h-4 w-4 mr-2" />
                                      Copy
                                    </Button>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      onClick={() => downloadJson(extractedData, `${document.fileName}_extracted.json`)}
                                    >
                                      <Download className="h-4 w-4 mr-2" />
                                      JSON
                                    </Button>
                                  </>
                                ) : (
                                  <>
                                    <Button
                                      variant="default"
                                      size="sm"
                                      onClick={async () => {
                                        try {
                                          // Validate JSON
                                          const parsed = JSON.parse(editedJson)
                                          setIsSavingEdit(true)

                                          // Submit correction via API with the new format
                                          await backendClient.submitCorrection(
                                            document.id,
                                            parsed,
                                            editNotes.trim() || "Full extraction edit",
                                            "anonymous"
                                          )

                                          toast.success("Correction saved successfully!")
                                          setIsEditMode(false)
                                          setEditNotes("")
                                          loadFullDocument()
                                          loadCorrections()
                                        } catch (error) {
                                          if (error instanceof SyntaxError) {
                                            toast.error("Invalid JSON format")
                                          } else {
                                            toast.error("Failed to save correction")
                                          }
                                        } finally {
                                          setIsSavingEdit(false)
                                        }
                                      }}
                                      disabled={isSavingEdit}
                                    >
                                      {isSavingEdit ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                      ) : (
                                        <Save className="h-4 w-4 mr-2" />
                                      )}
                                      Save
                                    </Button>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      onClick={() => setIsEditMode(false)}
                                    >
                                      <X className="h-4 w-4 mr-2" />
                                      Cancel
                                    </Button>
                                  </>
                                )}
                              </div>
                            </div>
                            {isEditMode && (
                              <CardDescription className="text-amber-600 dark:text-amber-400">
                                🔧 Edit Mode - Make your corrections below
                              </CardDescription>
                            )}
                          </CardHeader>
                          <CardContent className="flex-1 overflow-auto p-4">
                            {isEditMode ? (
                              <div className="flex flex-col h-full gap-3">
                                <div>
                                  <Label className="text-sm text-muted-foreground">Notes (optional)</Label>
                                  <Input
                                    value={editNotes}
                                    onChange={(e) => setEditNotes(e.target.value)}
                                    placeholder="Describe your changes..."
                                    className="mt-1"
                                  />
                                </div>
                                <Textarea
                                  value={editedJson}
                                  onChange={(e) => setEditedJson(e.target.value)}
                                  className="flex-1 min-h-[350px] font-mono text-sm resize-none"
                                  placeholder="Enter JSON..."
                                />
                              </div>
                            ) : (
                              <pre className="text-sm bg-muted p-4 rounded-lg whitespace-pre-wrap break-words">
                                {extractedData
                                  ? JSON.stringify(extractedData, null, 2)
                                  : "No extracted data available"}
                              </pre>
                            )}
                          </CardContent>
                        </Card>
                      </TabsContent>

                      {/* Evaluation Tab */}
                      <TabsContent value="evaluation" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                        <Card className="flex flex-col h-full overflow-hidden">
                          <CardHeader className="pb-3 flex-shrink-0">
                            <div className="flex items-center justify-between">
                              <CardTitle className="text-lg">Evaluation Results</CardTitle>
                              <div className="flex gap-2">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => copyToClipboard(JSON.stringify(evaluationData, null, 2))}
                                  disabled={!evaluationData}
                                >
                                  <Copy className="h-4 w-4 mr-2" />
                                  Copy
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => downloadJson(evaluationData, `${document.fileName}_evaluation.json`)}
                                  disabled={!evaluationData}
                                >
                                  <Download className="h-4 w-4 mr-2" />
                                  JSON
                                </Button>
                              </div>
                            </div>
                          </CardHeader>
                          <CardContent className="flex-1 overflow-auto p-4">
                            {evaluationData ? (
                              <pre className="text-sm bg-muted p-4 rounded-lg whitespace-pre-wrap break-words">
                                {JSON.stringify(evaluationData, null, 2)}
                              </pre>
                            ) : (
                              <div className="h-full flex items-center justify-center text-muted-foreground">
                                <div className="text-center">
                                  <CheckCircle className="h-12 w-12 mx-auto mb-2 opacity-50" />
                                  <p>No evaluation data available</p>
                                </div>
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </TabsContent>

                      {/* Confidence Tab */}
                      <TabsContent value="confidence" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                        <Card className="flex flex-col h-full overflow-hidden">
                          <CardHeader className="pb-3 flex-shrink-0">
                            <div className="flex items-center justify-between">
                              <CardTitle className="text-lg">Field Confidence</CardTitle>
                              <div className="flex items-center gap-2">
                                {confidenceMean != null && (
                                  <Badge variant="outline">Mean {(confidenceMean * 100).toFixed(1)}%</Badge>
                                )}
                                {ocrConfidence?.mean != null && (
                                  <Badge variant="outline" title="Aggregated OCR word-recognition confidence">
                                    OCR {(ocrConfidence.mean * 100).toFixed(1)}%
                                  </Badge>
                                )}
                              </div>
                            </div>
                            <CardDescription>
                              Per-field extraction confidence (0–100%). Lowest-confidence fields are listed first for review.
                            </CardDescription>
                          </CardHeader>
                          <CardContent className="flex-1 overflow-auto p-0">
                            {confidenceRows.length > 0 ? (
                              <Table>
                                <TableHeader className="sticky top-0 bg-background z-10">
                                  <TableRow>
                                    <TableHead>Field</TableHead>
                                    <TableHead>Value</TableHead>
                                    <TableHead className="w-[190px]">Confidence</TableHead>
                                  </TableRow>
                                </TableHeader>
                                <TableBody>
                                  {confidenceRows.map((row) => {
                                    const pct = Math.round(row.score * 100)
                                    const c = confidenceColor(row.score)
                                    return (
                                      <TableRow key={row.path}>
                                        <TableCell className="font-mono text-xs align-top">{row.path}</TableCell>
                                        <TableCell
                                          className="text-xs align-top max-w-[220px] truncate"
                                          title={row.value}
                                        >
                                          {row.value || "—"}
                                        </TableCell>
                                        <TableCell className="align-top">
                                          <div className="flex items-center gap-2">
                                            <div className="h-2 flex-1 rounded-full bg-muted overflow-hidden">
                                              <div className={`h-full ${c.bar}`} style={{ width: `${pct}%` }} />
                                            </div>
                                            <span className={`text-xs font-medium tabular-nums w-9 text-right ${c.text}`}>
                                              {pct}%
                                            </span>
                                          </div>
                                        </TableCell>
                                      </TableRow>
                                    )
                                  })}
                                </TableBody>
                              </Table>
                            ) : (
                              <div className="h-full flex items-center justify-center text-muted-foreground p-8">
                                <div className="text-center">
                                  <Gauge className="h-12 w-12 mx-auto mb-2 opacity-50" />
                                  <p>No field-level confidence available</p>
                                  <p className="text-xs mt-1">
                                    Field confidence is produced by the Content Understanding extraction backend.
                                  </p>
                                </div>
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </TabsContent>

                      <TabsContent value="pages" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                        <PageAnalysisPanel
                          metrics={pageMetrics}
                          chunks={cuChunks}
                          onSelectPage={setCurrentPage}
                        />
                      </TabsContent>

                      {/* OCR Tab */}
                      <TabsContent value="ocr" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                        <Card className="flex flex-col h-full overflow-hidden">
                          <CardHeader className="pb-3 flex-shrink-0">
                            <div className="flex items-center justify-between">
                              <CardTitle className="text-lg">OCR Output</CardTitle>
                              <div className="flex items-center gap-2">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => {
                                    if (ocrText) {
                                      const blob = new Blob([ocrText], { type: 'text/markdown' })
                                      const url = URL.createObjectURL(blob)
                                      const a = window.document.createElement('a')
                                      a.href = url
                                      a.download = `${document.fileName.replace(/\.[^/.]+$/, '')}_ocr.md`
                                      window.document.body.appendChild(a)
                                      a.click()
                                      window.document.body.removeChild(a)
                                      URL.revokeObjectURL(url)
                                      toast.success('OCR markdown downloaded')
                                    }
                                  }}
                                  disabled={!ocrText}
                                >
                                  <Download className="h-4 w-4 mr-2" />
                                  Download MD
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => copyToClipboard(ocrText || "")}
                                >
                                  <Copy className="h-4 w-4 mr-2" />
                                  Copy
                                </Button>
                              </div>
                            </div>
                          </CardHeader>
                          <CardContent className="flex-1 overflow-auto p-4">
                            {ocrText ? (
                              <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:font-semibold prose-p:my-2 prose-table:text-sm">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {ocrText}
                                </ReactMarkdown>
                              </div>
                            ) : (
                              <div className="text-center py-12 text-muted-foreground">
                                No OCR data available
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </TabsContent>

                      {/* Summary Tab */}
                      <TabsContent value="summary" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                        <Card className="flex flex-col h-full overflow-hidden">
                          <CardHeader className="pb-3 flex-shrink-0">
                            <CardTitle className="text-lg">Document Summary</CardTitle>
                          </CardHeader>
                          <CardContent className="flex-1 overflow-auto p-4">
                            {summaryData ? (
                              <div className="prose prose-sm dark:prose-invert max-w-none">
                                {summaryData}
                              </div>
                            ) : (
                              <div className="text-center py-12 text-muted-foreground">
                                No summary available
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </TabsContent>

                      {/* Processing Details Tab */}
                      <TabsContent value="details" className="absolute inset-0 m-0 p-4 overflow-auto data-[state=inactive]:hidden">
                        <div className="grid gap-4">
                        {/* Timing Information */}
                        <Card>
                          <CardHeader className="pb-3">
                            <CardTitle className="text-lg flex items-center gap-2">
                              <Clock className="h-5 w-5" />
                              Processing Times
                            </CardTitle>
                          </CardHeader>
                          <CardContent>
                            <div className="space-y-4">
                              {/* Total Time */}
                              <div className="bg-muted p-4 rounded-lg">
                                <div className="text-sm text-muted-foreground">Total Processing Time</div>
                                <div className="text-2xl font-bold">
                                  {processingProperties?.total_time_seconds
                                    ? `${Number(processingProperties.total_time_seconds).toFixed(2)}s`
                                    : document.totalTime
                                      ? `${document.totalTime.toFixed(2)}s`
                                      : "N/A"}
                                </div>
                              </div>

                              {/* Step Timings */}
                              <div className="space-y-2">
                                <ProcessingTimeRow
                                  label="File Upload"
                                  time={processingState?.file_landed_time_seconds as number}
                                  completed={document.fileLanded}
                                />
                                <ProcessingTimeRow
                                  label="OCR Processing"
                                  time={processingState?.ocr_completed_time_seconds as number}
                                  completed={document.ocrCompleted}
                                />
                                <ProcessingTimeRow
                                  label="GPT Extraction"
                                  time={processingState?.gpt_extraction_completed_time_seconds as number}
                                  completed={document.gptExtraction}
                                />
                                <ProcessingTimeRow
                                  label="GPT Evaluation"
                                  time={processingState?.gpt_evaluation_completed_time_seconds as number}
                                  completed={document.gptEvaluation}
                                />
                                <ProcessingTimeRow
                                  label="GPT Summary"
                                  time={processingState?.gpt_summary_completed_time_seconds as number}
                                  completed={document.gptSummary}
                                />
                              </div>
                            </div>
                          </CardContent>
                        </Card>

                        {/* Document Properties */}
                        <Card>
                          <CardHeader className="pb-3">
                            <CardTitle className="text-lg flex items-center gap-2">
                              <FileText className="h-5 w-5" />
                              Document Properties
                            </CardTitle>
                          </CardHeader>
                          <CardContent>
                            <div className="space-y-3">
                              <PropertyRow label="Document ID" value={document.id} />
                              <PropertyRow label="Dataset" value={document.dataset} />
                              <PropertyRow label="File Name" value={document.fileName} />
                              <PropertyRow
                                label="File Size"
                                value={processingProperties?.blob_size
                                  ? formatBytes(Number(processingProperties.blob_size))
                                  : document.size
                                    ? formatBytes(document.size)
                                    : "N/A"
                                }
                              />
                              <PropertyRow
                                label="Pages"
                                value={processingProperties?.num_pages?.toString() || document.pages?.toString() || "N/A"}
                              />
                              <PropertyRow
                                label="Blob Path"
                                value={processingProperties?.blob_name as string || "N/A"}
                              />
                              <PropertyRow
                                label="Processed At"
                                value={processingProperties?.request_timestamp
                                  ? formatDate(new Date(processingProperties.request_timestamp as string))
                                  : formatDate(document.timestamp)
                                }
                              />
                            </div>
                          </CardContent>
                        </Card>

                        {/* Model Configuration */}
                        <Card>
                          <CardHeader className="pb-3">
                            <CardTitle className="text-lg flex items-center gap-2">
                              <Settings className="h-5 w-5" />
                              Model Configuration
                            </CardTitle>
                          </CardHeader>
                          <CardContent>
                            {fullDocument?.model_input ? (
                              <div className="space-y-3">
                                <PropertyRow
                                  label="Model Deployment"
                                  value={(fullDocument.model_input as Record<string, unknown>)?.model_deployment as string || "N/A"}
                                />
                                <PropertyRow
                                  label="System Prompt"
                                  value={(fullDocument.model_input as Record<string, unknown>)?.model_prompt as string || "N/A"}
                                />
                                <PropertyRow
                                  label="Max Pages Per Chunk"
                                  value={String((fullDocument.model_input as Record<string, unknown>)?.max_pages_per_chunk ?? "N/A")}
                                />
                                {(() => {
                                  const modelInput = fullDocument.model_input as Record<string, unknown>
                                  const exampleSchema = modelInput?.example_schema
                                  if (exampleSchema && typeof exampleSchema === 'object' && Object.keys(exampleSchema).length > 0) {
                                    return (
                                      <div className="pt-2">
                                        <span className="text-sm text-muted-foreground">Output Schema</span>
                                        <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto max-h-32">
                                          {JSON.stringify(exampleSchema, null, 2)}
                                        </pre>
                                      </div>
                                    )
                                  }
                                  return null
                                })()}
                              </div>
                            ) : (
                              <div className="text-sm text-muted-foreground">No model configuration available</div>
                            )}

                            {/* Processing Options */}
                            {fullDocument?.processing_options && (
                              <div className="mt-6">
                                <h4 className="font-medium mb-3">Processing Options</h4>
                                <div className="flex flex-wrap gap-2">
                                  {Object.entries(fullDocument.processing_options as Record<string, boolean>).map(([key, value]) => (
                                    <Badge
                                      key={key}
                                      variant={value ? "default" : "outline"}
                                      className={!value ? "opacity-50" : ""}
                                    >
                                      {key.replace(/_/g, ' ').replace(/\w/g, l => l.toUpperCase())}
                                      {value ? " ✓" : " ✗"}
                                    </Badge>
                                  ))}
                                </div>
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </div>
                    </TabsContent>

                    {/* Cost Tab */}
                    <TabsContent value="cost" className="absolute inset-0 m-0 p-4 overflow-auto data-[state=inactive]:hidden">
                      <CostPanel
                        cost={documentCost}
                        tier={tierUsed}
                        backend={extractionBackendUsed}
                        cuFallback={cuFallback}
                        cuUsage={cuUsage}
                      />
                    </TabsContent>

                    {/* Chat Tab */}
                    <TabsContent value="chat" className="absolute inset-0 m-0 p-4 flex flex-col data-[state=inactive]:hidden">
                      <Card className="h-full flex flex-col">
                        <CardHeader className="pb-3 flex-shrink-0">
                          <CardTitle className="text-lg">Chat with Document</CardTitle>
                          <CardDescription>
                            Ask questions about the document content
                          </CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1 flex flex-col min-h-0 pb-4">
                          <div className="flex-1 overflow-y-auto mb-4 border rounded-lg p-3 bg-muted/20">
                            <div className="space-y-4">
                              {chatMessages.length === 0 ? (
                                <div className="text-center py-8 text-muted-foreground">
                                  <MessageSquare className="h-12 w-12 mx-auto mb-2 opacity-50" />
                                  <p>Start a conversation about this document</p>
                                </div>
                              ) : (
                                chatMessages.map((msg, i) => (
                                  <div
                                    key={i}
                                    className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                                  >
                                    <div
                                      className={`max-w-[85%] p-3 rounded-lg ${
                                        msg.role === "user"
                                          ? "bg-primary text-primary-foreground"
                                          : "bg-background border shadow-sm"
                                      }`}
                                    >
                                      {msg.role === "assistant" ? (
                                        <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 prose-table:text-xs prose-th:px-2 prose-td:px-2 prose-th:py-1 prose-td:py-1 prose-table:border prose-th:border prose-td:border">
                                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                            {msg.content}
                                          </ReactMarkdown>
                                        </div>
                                      ) : (
                                        <span className="whitespace-pre-wrap">{msg.content}</span>
                                      )}
                                    </div>
                                  </div>
                                ))
                              )}
                              {isSending && (
                                <div className="flex justify-start">
                                  <div className="bg-background border shadow-sm p-3 rounded-lg">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="flex gap-2 flex-shrink-0">
                            <Input
                              placeholder="Ask a question..."
                              value={chatInput}
                              onChange={(e) => setChatInput(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.shiftKey) {
                                  e.preventDefault()
                                  sendMessage()
                                }
                              }}
                            />
                            <Button onClick={sendMessage} disabled={!chatInput.trim() || isSending}>
                              <Send className="h-4 w-4" />
                            </Button>
                          </div>
                        </CardContent>
                      </Card>
                    </TabsContent>

                    {/* Corrections Tab */}
                    <TabsContent value="corrections" className="absolute inset-0 m-0 p-4 overflow-hidden data-[state=inactive]:hidden">
                      <div className="h-full flex flex-col">
                        {/* Correction History */}
                        <Card className="flex-1 flex flex-col overflow-hidden">
                          <CardHeader className="pb-2 flex-shrink-0">
                            <CardTitle className="text-base">Correction History</CardTitle>
                          </CardHeader>
                          <CardContent className="pt-0 flex-1 overflow-auto">
                            {!fullDocument?.extracted_data ? (
                              <div className="text-center py-4 text-sm text-muted-foreground">
                                <p>No data available</p>
                              </div>
                            ) : (
                              <div className="space-y-2">
                                {/* Original Document - First */}
                                <div className="border rounded-lg p-3 bg-muted/30">
                                  <div className="flex items-center justify-between mb-2">
                                    <div className="flex items-center gap-2">
                                      <Badge variant="outline" className="text-xs">Original</Badge>
                                      <span className="text-xs text-muted-foreground">
                                        Initial extraction
                                      </span>
                                    </div>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      onClick={() => downloadJson(
                                        corrections.length > 0
                                          ? corrections[0].original_data
                                          : (fullDocument.extracted_data?.gpt_extraction_output || fullDocument.extracted_data),
                                        `${document.fileName}_original.json`
                                      )}
                                    >
                                      <Download className="h-3 w-3 mr-1" />
                                      Download
                                    </Button>
                                  </div>
                                  <div className="text-xs">
                                    <pre className="bg-background p-2 rounded mt-1 text-[11px] overflow-auto max-h-40">
                                      {JSON.stringify(
                                        corrections.length > 0
                                          ? corrections[0].original_data
                                          : (fullDocument.extracted_data?.gpt_extraction_output || fullDocument.extracted_data),
                                        null,
                                        2
                                      )}
                                    </pre>
                                  </div>
                                </div>

                                {/* Corrections */}
                                {corrections.length === 0 ? (
                                  <div className="text-center py-2 text-xs text-muted-foreground">
                                    No corrections yet
                                  </div>
                                ) : (
                                  corrections.map((correction, index) => (
                                    <div
                                      key={`correction-${correction.correction_number}-${index}`}
                                      className="border rounded-lg p-3"
                                    >
                                      <div className="flex items-center justify-between mb-1">
                                        <div className="flex items-center gap-2">
                                          <Badge className="text-xs">#{correction.correction_number}</Badge>
                                          <span className="text-xs text-muted-foreground">
                                            {formatDate(correction.timestamp)}
                                          </span>
                                        </div>
                                        <Button
                                          variant="outline"
                                          size="sm"
                                          onClick={() => downloadJson(
                                            correction.corrected_data,
                                            `${document.fileName}_correction_${correction.correction_number}.json`
                                          )}
                                        >
                                          <Download className="h-3 w-3 mr-1" />
                                          Download
                                        </Button>
                                      </div>
                                      {correction.notes && (
                                        <p className="text-xs text-muted-foreground mb-1 italic">
                                          {correction.notes}
                                        </p>
                                      )}
                                      <div className="text-xs">
                                        <pre className="bg-muted p-2 rounded mt-1 text-[11px] overflow-auto max-h-40">
                                          {JSON.stringify(correction.corrected_data, null, 2)}
                                        </pre>
                                      </div>
                                    </div>
                                  ))
                                )}
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </div>
                    </TabsContent>
                  </>
                )}
              </div>
            </Tabs>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}

function PricingSourceBadge({ source }: { source: Cost["pricing_source"] }) {
  if (source === "azure_retail") {
    return <Badge className="bg-green-500 hover:bg-green-500">Live pricing</Badge>
  }
  if (source === "mixed") {
    return <Badge variant="outline">Mixed pricing</Badge>
  }
  return <Badge variant="secondary">Fallback pricing</Badge>
}

function ExtractionLineageBadge({ backend }: { backend?: ExtractionBackend }) {
  if (!backend) return null
  if (backend === "content_understanding") {
    return <Badge className="bg-sky-500 hover:bg-sky-500">Content Understanding</Badge>
  }
  if (backend === "content_understanding+gpt") {
    return <Badge className="bg-amber-500 hover:bg-amber-500">CU + GPT fallback</Badge>
  }
  if (backend === "skipped_paddle_pregate") {
    return <Badge variant="secondary">Skipped (Paddle pre-gate)</Badge>
  }
  return <Badge variant="outline">GPT</Badge>
}

function cuMeterTooltip(meter: string): string {
  switch (meter) {
    case "minimal":
      return "Minimal meter: digital documents (DOCX/XLSX/HTML/TXT), no OCR or layout - lowest cost."
    case "basic":
      return "Basic meter: image-based documents processed with OCR (read) only."
    case "standard":
      return "Standard meter: image-based documents with layout analysis (tables + structure)."
    default:
      return "Content Understanding content-extraction meter tier."
  }
}

/** Human label for the Content Understanding call depth. */
function cuCallTypeLabel(callType: string): string {
  switch (callType) {
    case "content_extraction":
      return "Content extraction"
    case "field_extraction":
      return "Field extraction"
    case "end_to_end":
      return "End-to-end"
    default:
      return callType
  }
}

/** Tooltip describing what each Content Understanding call type bills for. */
function cuCallTypeTooltip(callType: string): string {
  switch (callType) {
    case "content_extraction":
      return "Content extraction only: OCR/layout of the document, no field extraction or generative model calls (lowest cost)."
    case "field_extraction":
      return "Field extraction: structured fields resolved from the schema (contextualization), without generative LLM tokens."
    case "end_to_end":
      return "End-to-end: content extraction + schema field extraction + generative LLM reasoning in a single Content Understanding call (full analyzer)."
    default:
      return "Content Understanding call type."
  }
}

function PageAnalysisPanel({
  metrics,
  chunks,
  onSelectPage,
}: {
  metrics: PageMetric[]
  chunks: CuChunkEvidence[]
  onSelectPage: (page: number) => void
}) {
  const [selectedPage, setSelectedPage] = React.useState<number | null>(metrics[0]?.page_number ?? null)

  React.useEffect(() => {
    setSelectedPage(metrics[0]?.page_number ?? null)
  }, [metrics])

  const selectedMetric = metrics.find((metric) => metric.page_number === selectedPage) ?? null
  const selectedChunk = chunks.find((chunk) => chunk.id === selectedMetric?.chunk_id) ?? null

  if (!metrics.length) {
    return (
      <Card className="flex h-full flex-col overflow-hidden">
        <CardHeader>
          <CardTitle className="text-lg">Page Analysis</CardTitle>
          <CardDescription>Cost, confidence, and extraction evidence by page</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-1 items-center justify-center text-center text-muted-foreground">
          <div>
            <FileText className="mx-auto mb-2 h-12 w-12 opacity-50" />
            <p>No Content Understanding page metrics available</p>
            <p className="mt-1 text-xs">Page metrics are recorded for newly processed CU documents.</p>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="flex h-full flex-col overflow-hidden">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">Page Analysis</CardTitle>
        <CardDescription>
          Exact CU meter charges plus contextualization and model spend allocated equally within each CU chunk.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex-1 space-y-4 overflow-auto">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-background">
            <TableRow>
              <TableHead>Page</TableHead>
              <TableHead>CU</TableHead>
              <TableHead className="text-right">Net meter</TableHead>
              <TableHead className="text-right">Net shared</TableHead>
              <TableHead className="text-right">Net all-in</TableHead>
              <TableHead className="text-right">Confidence</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {metrics.map((metric) => {
              const chunk = chunks.find((candidate) => candidate.id === metric.chunk_id)
              const sharedCost = metric.allocated_contextualization_usd + metric.allocated_llm_usd
              return (
                <TableRow
                  key={metric.page_number}
                  className={`cursor-pointer ${selectedPage === metric.page_number ? "bg-muted" : ""}`}
                  onClick={() => {
                    setSelectedPage(metric.page_number)
                    onSelectPage(metric.page_number)
                  }}
                >
                  <TableCell className="font-medium">{metric.page_number}</TableCell>
                  <TableCell className="capitalize text-xs">{metric.meter || "N/A"}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{formatUsd(metric.allocated_meter_usd)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{formatUsd(sharedCost)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{formatUsd(metric.estimated_all_in_usd)}</TableCell>
                  <TableCell className="text-right text-xs">
                    {chunk?.confidence.mean != null ? `${Math.round(chunk.confidence.mean * 100)}%` : "N/A"}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>

        {selectedMetric && selectedChunk && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">Page {selectedMetric.page_number}</Badge>
                <Badge variant="outline">{selectedChunk.id.replace("_", " ")}</Badge>
                <Badge variant="secondary">Chunk-level provenance</Badge>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded bg-muted p-2">
                  <div className="text-muted-foreground">CU meter</div>
                  <div className="font-mono">{formatUsd(selectedMetric.allocated_meter_usd)}</div>
                </div>
                <div className="rounded bg-muted p-2">
                  <div className="text-muted-foreground">Shared allocation</div>
                  <div className="font-mono">
                    {formatUsd(selectedMetric.allocated_contextualization_usd + selectedMetric.allocated_llm_usd)}
                  </div>
                </div>
                <div className="rounded bg-muted p-2">
                  <div className="text-muted-foreground">Mean confidence</div>
                  <div>{selectedChunk.confidence.mean != null ? `${(selectedChunk.confidence.mean * 100).toFixed(1)}%` : "N/A"}</div>
                </div>
                <div className="rounded bg-muted p-2">
                  <div className="text-muted-foreground">Lowest confidence</div>
                  <div>{selectedChunk.confidence.min != null ? `${(selectedChunk.confidence.min * 100).toFixed(1)}%` : "N/A"}</div>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                CU analyzed pages {selectedChunk.page_start}-{selectedChunk.page_end} together. Field evidence applies to the chunk because CU did not return reliable field-level page spans.
              </p>
            </div>
            <div className="space-y-2 rounded-lg border p-3">
              <div className="text-sm font-medium">Chunk Extraction Output</div>
              <pre className="max-h-52 overflow-auto rounded bg-muted p-3 text-xs">
                {JSON.stringify(selectedChunk.output, null, 2)}
              </pre>
              {Object.keys(selectedChunk.field_confidence).length > 0 && (
                <details>
                  <summary className="cursor-pointer text-sm font-medium">
                    Field confidence ({selectedChunk.confidence.field_count})
                  </summary>
                  <div className="mt-2 space-y-1">
                    {Object.entries(selectedChunk.field_confidence)
                      .sort(([, firstScore], [, secondScore]) => firstScore - secondScore)
                      .map(([path, score]) => (
                        <div key={path} className="flex items-center justify-between gap-3 rounded bg-muted px-2 py-1 text-xs">
                          <span className="min-w-0 truncate font-mono" title={path}>{path}</span>
                          <span className={confidenceColor(score).text}>{(score * 100).toFixed(1)}%</span>
                        </div>
                      ))}
                  </div>
                </details>
              )}
              {selectedChunk.ocr_markdown && (
                <details>
                  <summary className="cursor-pointer text-sm font-medium">CU markdown</summary>
                  <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted p-3 text-xs whitespace-pre-wrap">
                    {selectedChunk.ocr_markdown}
                  </pre>
                </details>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function CostPanel({
  cost,
  tier,
  backend,
  cuFallback,
  cuUsage,
}: {
  cost?: Cost
  tier?: string
  backend?: ExtractionBackend
  cuFallback?: CuFallback
  cuUsage?: CuUsage
}) {
  if (!cost) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <DollarSign className="h-5 w-5" />
            Cost
          </CardTitle>
        </CardHeader>
        <CardContent className="py-12 text-center text-muted-foreground">
          Cost data not available
        </CardContent>
      </Card>
    )
  }

  const totalTokens = (cost.total_input_tokens || 0) + (cost.total_output_tokens || 0)
  const modelBreakdown = Object.entries(cost.model_breakdown || {}).sort(([, a], [, b]) => b - a)
  const discountPct = cost.discount_pct ?? 0
  const hasDiscount = discountPct > 0 && cost.list_total_usd != null
  const consumptionAvailable = cost.consumption_available === true
  const fallbackReasons = cuFallback?.reasons?.filter(Boolean) ?? []

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="text-lg flex items-center gap-2">
              <DollarSign className="h-5 w-5" />
              Cost Summary
            </CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <ExtractionLineageBadge backend={backend} />
              <PricingSourceBadge source={cost.pricing_source} />
            </div>
          </div>
          <CardDescription>Usage and estimated processing cost for this document</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg bg-muted p-4">
              <div className="text-sm text-muted-foreground">
                {hasDiscount ? `Net Cost (${discountPct}% off)` : "Total Cost"}
              </div>
              <div className="text-2xl font-bold">{formatUsd(cost.total_usd)}</div>
              {hasDiscount && (
                <div className="text-xs text-muted-foreground mt-1">
                  List <span className="line-through">{formatUsd(cost.list_total_usd)}</span>
                </div>
              )}
            </div>
            <div className="rounded-lg bg-muted p-4">
              <div className="text-sm text-muted-foreground">Cost / Page</div>
              <div className="text-2xl font-bold">{formatUsd(cost.usd_per_page)}</div>
              {hasDiscount && cost.list_usd_per_page != null && (
                <div className="text-xs text-muted-foreground mt-1">
                  List <span className="line-through">{formatUsd(cost.list_usd_per_page)}</span>
                </div>
              )}
            </div>
            <div className="rounded-lg bg-muted p-4">
              <div className="text-sm text-muted-foreground">Input Tokens</div>
              <div className="text-xl font-semibold">{formatNumber(cost.total_input_tokens)}</div>
            </div>
            <div className="rounded-lg bg-muted p-4">
              <div className="text-sm text-muted-foreground">Output Tokens</div>
              <div className="text-xl font-semibold">{formatNumber(cost.total_output_tokens)}</div>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Badge variant="outline" className="capitalize">Tier: {tier || "N/A"}</Badge>
            {cuUsage?.call_type && (
              <Badge className="bg-sky-500 hover:bg-sky-500" title={cuCallTypeTooltip(cuUsage.call_type)}>
                CU call: {cuCallTypeLabel(cuUsage.call_type)}
              </Badge>
            )}
            {cuUsage?.meter && (
              <Badge variant="outline" className="capitalize" title={cuMeterTooltip(cuUsage.meter)}>
                CU level: {cuUsage.meter}
              </Badge>
            )}
            {cuUsage?.contextualization_tokens ? (
              <Badge variant="outline">
                {formatNumber(cuUsage.contextualization_tokens)} contextualization tokens
              </Badge>
            ) : null}
            <Badge variant="outline">{formatNumber(totalTokens)} total tokens</Badge>
            {consumptionAvailable && (
              <Badge className="bg-emerald-500 hover:bg-emerald-500">Consumption pricing available</Badge>
            )}
          </div>
          {backend === "content_understanding+gpt" && (
            <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <div className="font-medium text-amber-700 dark:text-amber-400">
                Content Understanding fell back to GPT
              </div>
              <div className="text-muted-foreground mt-1">
                Low-confidence CU output triggered a silent GPT re-extraction. Cost reflects both passes.
                {fallbackReasons.length > 0 && (
                  <span> Reasons: {fallbackReasons.join(", ")}.</span>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Per-Stage Breakdown</CardTitle>
          <CardDescription>
            Model, token usage, and {hasDiscount ? "agreement-adjusted " : ""}cost by pipeline stage
          </CardDescription>
        </CardHeader>
        <CardContent>
          {cost.per_stage?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Stage</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">Input</TableHead>
                  <TableHead className="text-right">Output</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {cost.per_stage.map((stage, index) => (
                  <TableRow key={`${stage.stage}-${stage.model}-${index}`}>
                    <TableCell className="font-medium">{formatStageName(stage.stage)}</TableCell>
                    <TableCell className="max-w-[180px] truncate" title={stage.model}>{stage.model}</TableCell>
                    <TableCell className="text-right font-mono">{formatNumber(stage.input_tokens)}</TableCell>
                    <TableCell className="text-right font-mono">{formatNumber(stage.output_tokens)}</TableCell>
                    <TableCell className="text-right font-mono">{formatUsd(stage.usd)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="py-6 text-center text-muted-foreground">No stage-level cost data available</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Model Breakdown</CardTitle>
          <CardDescription>Cost contribution by model</CardDescription>
        </CardHeader>
        <CardContent>
          {modelBreakdown.length ? (
            <div className="space-y-2">
              {modelBreakdown.map(([model, usd]) => (
                <div key={model} className="flex items-center justify-between gap-3 rounded-lg border p-3">
                  <span className="text-sm font-medium truncate" title={model}>{model}</span>
                  <span className="font-mono text-sm">{formatUsd(usd)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-6 text-center text-muted-foreground">No model breakdown available</div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function ProcessingStep({ label, completed, hasError }: { label: string; completed: boolean; hasError?: boolean }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className={`w-10 h-10 rounded-full flex items-center justify-center transition-all ${
          completed
            ? "bg-green-100 text-green-600 dark:bg-green-900 dark:text-green-400 ring-2 ring-green-500/20"
            : hasError
            ? "bg-red-100 text-red-600 dark:bg-red-900 dark:text-red-400 ring-2 ring-red-500/20"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {completed ? (
          <CheckCircle className="h-5 w-5" />
        ) : hasError ? (
          <XCircle className="h-5 w-5" />
        ) : (
          <Clock className="h-5 w-5" />
        )}
      </div>
      <span className={`text-xs font-medium ${
        completed ? "text-green-600 dark:text-green-400" :
        hasError ? "text-red-600 dark:text-red-400" :
        "text-muted-foreground"
      }`}>
        {label}
      </span>
    </div>
  )
}

function ProcessingTimeRow({ label, time, completed }: { label: string; time?: number; completed: boolean }) {
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-0">
      <div className="flex items-center gap-2">
        {completed ? (
          <CheckCircle className="h-4 w-4 text-green-500" />
        ) : (
          <Clock className="h-4 w-4 text-muted-foreground" />
        )}
        <span className="text-sm">{label}</span>
      </div>
      <span className={`text-sm font-mono ${completed ? "text-foreground" : "text-muted-foreground"}`}>
        {time !== undefined ? `${time.toFixed(2)}s` : "—"}
      </span>
    </div>
  )
}

function PropertyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between py-2 border-b last:border-0 gap-1">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium truncate max-w-[250px]" title={value}>
        {value}
      </span>
    </div>
  )
}
