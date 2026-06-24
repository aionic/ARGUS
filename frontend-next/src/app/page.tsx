"use client"

import * as React from "react"
import { motion, AnimatePresence } from "framer-motion"
import { 
  Upload, 
  Save, 
  Plus, 
  FileText, 
  Eye, 
  FileSearch, 
  ClipboardCheck,
  Sparkles,
  HelpCircle,
  Info,
  Loader2,
  X,
  CheckCircle,
  AlertCircle
} from "lucide-react"
import { toast } from "sonner"

import { PageContainer } from "@/components/layout/page-container"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { 
  Select, 
  SelectContent, 
  SelectItem, 
  SelectTrigger, 
  SelectValue 
} from "@/components/ui/select"
import { Checkbox } from "@/components/ui/checkbox"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Separator } from "@/components/ui/separator"
import { 
  Accordion, 
  AccordionContent, 
  AccordionItem, 
  AccordionTrigger 
} from "@/components/ui/accordion"
import { 
  Tooltip, 
  TooltipContent, 
  TooltipTrigger 
} from "@/components/ui/tooltip"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import {
  backendClient,
  type Configuration,
  type DatasetConfig,
  type EffectiveConfig,
  type ProcessingOptions,
  type Tier,
} from "@/lib/api-client"

type ProcessingOptionState = Required<
  Pick<
    ProcessingOptions,
    | "include_ocr"
    | "include_images"
    | "enable_summary"
    | "enable_evaluation"
    | "use_rules_engine"
    | "enable_preprocessing"
  >
> & {
  extraction_backend: NonNullable<ProcessingOptions["extraction_backend"]>
  tier: Tier
  enable_enhancement: boolean
  skip_if_still_bad: boolean
}

type BooleanProcessingOptionKey =
  | "include_ocr"
  | "include_images"
  | "enable_summary"
  | "enable_evaluation"
  | "use_rules_engine"
  | "enable_preprocessing"

const TIER_OPTIONS: Array<{
  value: Tier
  label: string
  cost: string
  description: string
}> = [
  {
    value: "economy",
    label: "Economy",
    cost: "$",
    description:
      "Lowest cost. Rules engine + cheap model. No vision or evaluation. Best for clean, simple, high-volume docs.",
  },
  {
    value: "standard",
    label: "Standard",
    cost: "$$",
    description: "Balanced. Cheap model + OCR, optional vision. Good default.",
  },
  {
    value: "premium",
    label: "Premium",
    cost: "$$$",
    description:
      "Highest quality. Full vision model + evaluation. Best for complex/low-quality docs.",
  },
]

const DEFAULT_PROCESSING_OPTIONS: ProcessingOptionState = {
  include_ocr: true,
  include_images: true,
  enable_summary: true,
  enable_evaluation: false,
  extraction_backend: "gpt",
  tier: "standard",
  use_rules_engine: true,
  enable_preprocessing: false,
  enable_enhancement: true,
  skip_if_still_bad: false,
}

const TIER_PRESETS: Record<
  Tier,
  Pick<
    ProcessingOptionState,
    | "include_ocr"
    | "include_images"
    | "enable_summary"
    | "enable_evaluation"
    | "use_rules_engine"
    | "enable_preprocessing"
    | "extraction_backend"
  >
> = {
  economy: {
    include_ocr: true,
    include_images: false,
    enable_summary: true,
    enable_evaluation: false,
    use_rules_engine: true,
    enable_preprocessing: false,
    extraction_backend: "gpt",
  },
  standard: {
    include_ocr: true,
    include_images: true,
    enable_summary: true,
    enable_evaluation: false,
    use_rules_engine: true,
    enable_preprocessing: false,
    extraction_backend: "gpt",
  },
  premium: {
    include_ocr: true,
    include_images: true,
    enable_summary: true,
    enable_evaluation: true,
    use_rules_engine: false,
    enable_preprocessing: true,
    extraction_backend: "gpt",
  },
}

const CAPABILITY_TOGGLES: Array<{
  key: BooleanProcessingOptionKey
  label: string
  description: string
  icon: React.ElementType
}> = [
  {
    key: "include_ocr",
    label: "OCR",
    description: "Extract machine-readable text before AI extraction",
    icon: FileText,
  },
  {
    key: "include_images",
    label: "Vision / images",
    description: "Send page images to the extraction model",
    icon: Eye,
  },
  {
    key: "enable_summary",
    label: "Summary",
    description: "Generate a concise document summary",
    icon: FileSearch,
  },
  {
    key: "enable_evaluation",
    label: "Evaluation",
    description: "Run quality checks on extracted fields",
    icon: ClipboardCheck,
  },
  {
    key: "use_rules_engine",
    label: "Rules engine",
    description: "Use deterministic rules before model extraction",
    icon: CheckCircle,
  },
  {
    key: "enable_preprocessing",
    label: "Image preprocessing",
    description: "Improve image quality before extraction",
    icon: Sparkles,
  },
]

function applyTierPreset(options: ProcessingOptionState, tier: Tier): ProcessingOptionState {
  return {
    ...options,
    ...TIER_PRESETS[tier],
    tier,
  }
}

function buildProcessingOptionState(datasetConfig?: DatasetConfig): ProcessingOptionState {
  const savedOptions = datasetConfig?.processing_options ?? {}
  const effectiveConfig: EffectiveConfig | undefined = datasetConfig?.effective_config

  return {
    ...DEFAULT_PROCESSING_OPTIONS,
    tier: savedOptions.tier ?? datasetConfig?.tier ?? effectiveConfig?.tier ?? DEFAULT_PROCESSING_OPTIONS.tier,
    include_ocr:
      savedOptions.include_ocr ??
      savedOptions.enable_ocr ??
      effectiveConfig?.enable_ocr ??
      DEFAULT_PROCESSING_OPTIONS.include_ocr,
    include_images:
      savedOptions.include_images ??
      savedOptions.enable_images ??
      effectiveConfig?.enable_images ??
      DEFAULT_PROCESSING_OPTIONS.include_images,
    enable_summary:
      savedOptions.enable_summary ??
      effectiveConfig?.enable_summary ??
      DEFAULT_PROCESSING_OPTIONS.enable_summary,
    enable_evaluation:
      savedOptions.enable_evaluation ??
      effectiveConfig?.enable_evaluation ??
      DEFAULT_PROCESSING_OPTIONS.enable_evaluation,
    extraction_backend:
      savedOptions.extraction_backend ?? DEFAULT_PROCESSING_OPTIONS.extraction_backend,
    use_rules_engine:
      savedOptions.use_rules_engine ??
      datasetConfig?.use_rules_engine ??
      effectiveConfig?.use_rules_engine ??
      DEFAULT_PROCESSING_OPTIONS.use_rules_engine,
    enable_preprocessing:
      savedOptions.enable_preprocessing ??
      effectiveConfig?.enable_preprocessing ??
      DEFAULT_PROCESSING_OPTIONS.enable_preprocessing,
    enable_enhancement:
      savedOptions.enable_enhancement ?? DEFAULT_PROCESSING_OPTIONS.enable_enhancement,
    skip_if_still_bad:
      savedOptions.skip_if_still_bad ?? DEFAULT_PROCESSING_OPTIONS.skip_if_still_bad,
  }
}

interface TierControlsProps {
  idPrefix: string
  options: ProcessingOptionState
  onChange: (options: ProcessingOptionState) => void
}

function TierControls({ idPrefix, options, onChange }: TierControlsProps) {
  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label className="text-base">Extraction Tier</Label>
        <RadioGroup
          value={options.tier}
          onValueChange={(value) => onChange(applyTierPreset(options, value as Tier))}
          className="grid gap-2 md:grid-cols-3"
        >
          {TIER_OPTIONS.map((tier) => (
            <div
              key={tier.value}
              className={`rounded-lg border p-3 transition-colors ${
                options.tier === tier.value ? "border-primary bg-primary/5" : "border-border"
              }`}
            >
              <div className="flex items-start gap-2">
                <RadioGroupItem
                  value={tier.value}
                  id={`${idPrefix}_tier_${tier.value}`}
                  className="mt-1"
                />
                <Label
                  htmlFor={`${idPrefix}_tier_${tier.value}`}
                  className="flex flex-1 cursor-pointer flex-col gap-2"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-medium">{tier.label}</span>
                    <Badge variant="outline" className="font-mono">
                      {tier.cost}
                    </Badge>
                  </span>
                  <span className="text-xs leading-relaxed text-muted-foreground">
                    {tier.description}
                  </span>
                </Label>
              </div>
            </div>
          ))}
        </RadioGroup>
      </div>

      <Accordion type="single" collapsible className="rounded-lg border">
        <AccordionItem value="advanced-tier-overrides" className="border-0">
          <AccordionTrigger className="px-3 py-2 text-sm">
            Advanced capability overrides
          </AccordionTrigger>
          <AccordionContent className="space-y-4 px-3 pb-3">
            <div className="space-y-2">
              <Label htmlFor={`${idPrefix}_extraction_backend`} className="text-sm">
                Extraction Backend
              </Label>
              <Select
                value={options.extraction_backend}
                onValueChange={(value) =>
                  onChange({ ...options, extraction_backend: value })
                }
              >
                <SelectTrigger id={`${idPrefix}_extraction_backend`}>
                  <SelectValue placeholder="Select extraction backend" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="gpt">GPT (vision)</SelectItem>
                  <SelectItem value="content_understanding">Content Understanding</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {CAPABILITY_TOGGLES.map(({ key, label, description, icon: Icon }) => (
                <div key={key} className="flex items-start space-x-3">
                  <Checkbox
                    id={`${idPrefix}_${key}`}
                    checked={options[key]}
                    onCheckedChange={(checked) =>
                      onChange({ ...options, [key]: !!checked })
                    }
                  />
                  <div className="space-y-1">
                    <Label
                      htmlFor={`${idPrefix}_${key}`}
                      className="flex cursor-pointer items-center gap-2"
                    >
                      <Icon className="h-4 w-4" />
                      {label}
                    </Label>
                    <p className="text-xs text-muted-foreground">{description}</p>
                  </div>
                </div>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}

export default function ProcessFilesPage() {
  const [configuration, setConfiguration] = React.useState<Configuration | null>(null)
  const [selectedDataset, setSelectedDataset] = React.useState<string>("")
  const [modelPrompt, setModelPrompt] = React.useState("")
  const [exampleSchema, setExampleSchema] = React.useState("")
  const [maxPagesPerChunk, setMaxPagesPerChunk] = React.useState(10)
  const [processingOptions, setProcessingOptions] = React.useState<ProcessingOptionState>({
    ...DEFAULT_PROCESSING_OPTIONS,
  })
  const [files, setFiles] = React.useState<File[]>([])
  const [isLoading, setIsLoading] = React.useState(true)
  const [isSaving, setIsSaving] = React.useState(false)
  const [isUploading, setIsUploading] = React.useState(false)
  const [uploadProgress, setUploadProgress] = React.useState(0)

  // New dataset form state
  const [newDatasetName, setNewDatasetName] = React.useState("")
  const [newModelPrompt, setNewModelPrompt] = React.useState("Extract all data.")
  const [newExampleSchema, setNewExampleSchema] = React.useState("{}")
  const [newMaxPages, setNewMaxPages] = React.useState(10)
  const [newProcessingOptions, setNewProcessingOptions] = React.useState<ProcessingOptionState>({
    ...DEFAULT_PROCESSING_OPTIONS,
  })

  // Load configuration on mount
  React.useEffect(() => {
    loadConfiguration()
  }, [])

  // Update form when dataset changes
  React.useEffect(() => {
    if (selectedDataset && configuration?.datasets?.[selectedDataset]) {
      const datasetConfig = configuration.datasets[selectedDataset]
      setModelPrompt(datasetConfig.model_prompt || "")
      setExampleSchema(JSON.stringify(datasetConfig.example_schema || {}, null, 2))
      setMaxPagesPerChunk(datasetConfig.max_pages_per_chunk || 10)
      setProcessingOptions(buildProcessingOptionState(datasetConfig))
    }
  }, [selectedDataset, configuration])

  async function loadConfiguration() {
    setIsLoading(true)
    try {
      const config = await backendClient.getConfiguration()
      setConfiguration(config)
      const datasets = Object.keys(config.datasets || {})
      if (datasets.length > 0 && !selectedDataset) {
        setSelectedDataset(datasets[0])
      }
    } catch (error) {
      console.error("Failed to load configuration:", error)
      toast.error("Failed to load configuration")
    } finally {
      setIsLoading(false)
    }
  }

  async function handleSaveConfiguration() {
    if (!selectedDataset || !configuration) return

    setIsSaving(true)
    try {
      // Validate JSON
      let parsedSchema
      try {
        parsedSchema = JSON.parse(exampleSchema)
      } catch {
        toast.error("Invalid JSON in Example Schema")
        setIsSaving(false)
        return
      }

      // Update configuration
      const updatedConfig = {
        ...configuration,
        datasets: {
          ...configuration.datasets,
          [selectedDataset]: {
            ...configuration.datasets[selectedDataset],
            model_prompt: modelPrompt,
            example_schema: parsedSchema,
            max_pages_per_chunk: maxPagesPerChunk,
            processing_options: processingOptions
          }
        }
      }

      await backendClient.updateConfiguration(updatedConfig as unknown as Record<string, unknown>)
      setConfiguration(updatedConfig)
      toast.success("Configuration saved successfully!")
    } catch (error) {
      console.error("Failed to save configuration:", error)
      toast.error("Failed to save configuration")
    } finally {
      setIsSaving(false)
    }
  }

  async function handleAddDataset() {
    if (!newDatasetName.trim()) {
      toast.error("Please enter a dataset name")
      return
    }

    if (configuration?.datasets?.[newDatasetName]) {
      toast.error("Dataset already exists")
      return
    }

    try {
      // Validate JSON
      let parsedSchema
      try {
        parsedSchema = JSON.parse(newExampleSchema)
      } catch {
        toast.error("Invalid JSON in Example Schema")
        return
      }

      const baseConfig: Configuration = configuration ?? { datasets: {} }
      const updatedConfig: Configuration = {
        ...baseConfig,
        datasets: {
          ...baseConfig.datasets,
          [newDatasetName]: {
            model_prompt: newModelPrompt,
            example_schema: parsedSchema,
            max_pages_per_chunk: newMaxPages,
            processing_options: newProcessingOptions
          }
        }
      }

      await backendClient.updateConfiguration(updatedConfig as unknown as Record<string, unknown>)
      setConfiguration(updatedConfig)
      setSelectedDataset(newDatasetName)
      
      // Reset form
      setNewDatasetName("")
      setNewModelPrompt("Extract all data.")
      setNewExampleSchema("{}")
      setNewMaxPages(10)
      setNewProcessingOptions({ ...DEFAULT_PROCESSING_OPTIONS })

      toast.success(`Dataset "${newDatasetName}" created successfully!`)
    } catch (error) {
      console.error("Failed to create dataset:", error)
      toast.error("Failed to create dataset")
    }
  }

  async function handleUpload() {
    if (files.length === 0) {
      toast.error("Please select files to upload")
      return
    }

    if (!selectedDataset) {
      toast.error("Please select a dataset")
      return
    }

    setIsUploading(true)
    setUploadProgress(0)

    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        await backendClient.uploadFile(selectedDataset, file, {
          run_ocr: processingOptions.include_ocr,
          run_gpt_vision: processingOptions.include_images,
          run_summary: processingOptions.enable_summary,
          run_evaluation: processingOptions.enable_evaluation
        })
        setUploadProgress(((i + 1) / files.length) * 100)
      }

      toast.success(`Successfully uploaded ${files.length} file(s)!`, {
        description: "Processing will begin automatically."
      })
      setFiles([])
    } catch (error) {
      console.error("Upload failed:", error)
      toast.error("Failed to upload files")
    } finally {
      setIsUploading(false)
      setUploadProgress(0)
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) {
      setFiles(Array.from(e.target.files))
    }
  }

  function removeFile(index: number) {
    setFiles(files.filter((_, i) => i !== index))
  }

  // Validation: Ensure at least one of OCR or Images is enabled
  const isProcessingValid = processingOptions.include_ocr || processingOptions.include_images

  // Cost/performance indicator
  const enabledSteps = [
    processingOptions.include_ocr || processingOptions.include_images,
    processingOptions.enable_summary,
    processingOptions.enable_evaluation
  ].filter(Boolean).length

  if (isLoading) {
    return (
      <PageContainer>
        <div className="flex items-center justify-center min-h-[400px]">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </PageContainer>
    )
  }

  const datasetOptions = Object.keys(configuration?.datasets || {})

  return (
    <PageContainer
      title="🧠 Process Files"
      description="Upload and process documents with AI-powered extraction"
    >
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left Column - Dataset Configuration */}
        <div className="space-y-6">
          {/* Dataset Info Card */}
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-start gap-2">
                <Info className="h-5 w-5 text-blue-500 mt-0.5" />
                <div>
                  <CardTitle className="text-base">About Datasets</CardTitle>
                  <CardDescription>
                    Datasets are pre-configured profiles with custom AI prompts and schemas 
                    for different document types (invoices, contracts, etc.)
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>

          {/* Add New Dataset - Now before Dataset Configuration */}
          <Accordion type="single" collapsible>
            <AccordionItem value="new-dataset" className="border rounded-lg">
              <AccordionTrigger className="px-4">
                <div className="flex items-center gap-2">
                  <Plus className="h-4 w-4" />
                  Add New Dataset
                </div>
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                <div className="space-y-4 pt-2">
                  <div className="space-y-2">
                    <Label>Dataset Name</Label>
                    <Input
                      value={newDatasetName}
                      onChange={(e) => setNewDatasetName(e.target.value)}
                      placeholder="e.g., invoices, contracts..."
                    />
                  </div>

                  <div className="space-y-2">
                    <Label>Model Prompt</Label>
                    <Textarea
                      value={newModelPrompt}
                      onChange={(e) => setNewModelPrompt(e.target.value)}
                      className="min-h-[80px] font-mono text-sm"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label>Example Schema (JSON)</Label>
                    <Textarea
                      value={newExampleSchema}
                      onChange={(e) => setNewExampleSchema(e.target.value)}
                      className="min-h-[100px] font-mono text-sm"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label>Max Pages per Chunk</Label>
                    <Input
                      type="number"
                      min={1}
                      max={100}
                      value={newMaxPages}
                      onChange={(e) => setNewMaxPages(parseInt(e.target.value) || 10)}
                    />
                  </div>

                  <TierControls
                    idPrefix="new"
                    options={newProcessingOptions}
                    onChange={setNewProcessingOptions}
                  />

                  <Button 
                    onClick={handleAddDataset}
                    disabled={!newDatasetName.trim()}
                    className="w-full"
                  >
                    <Plus className="h-4 w-4 mr-2" />
                    Create Dataset
                  </Button>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>

          {/* Dataset Selection & Configuration */}
          <Card>
            <CardHeader>
              <CardTitle>Dataset Configuration</CardTitle>
              <CardDescription>
                Select a dataset and configure its extraction settings
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Dataset Selector */}
              <div className="space-y-2">
                <Label>Select Dataset</Label>
                <Select value={selectedDataset} onValueChange={setSelectedDataset}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a dataset..." />
                  </SelectTrigger>
                  <SelectContent>
                    {datasetOptions.map((dataset) => (
                      <SelectItem key={dataset} value={dataset}>
                        {dataset}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {selectedDataset && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="space-y-4"
                >
                  {/* Model Prompt */}
                  <div className="space-y-2">
                    <Label>Model Prompt</Label>
                    <Textarea
                      value={modelPrompt}
                      onChange={(e) => setModelPrompt(e.target.value)}
                      placeholder="Enter the extraction prompt..."
                      className="min-h-[120px] font-mono text-sm"
                    />
                  </div>

                  {/* Example Schema */}
                  <div className="space-y-2">
                    <Label>Example Schema (JSON)</Label>
                    <Textarea
                      value={exampleSchema}
                      onChange={(e) => setExampleSchema(e.target.value)}
                      placeholder='{"field": "value"}'
                      className="min-h-[200px] font-mono text-sm"
                    />
                  </div>

                  {/* Max Pages Per Chunk */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <Label>Document Chunk Size (pages)</Label>
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="h-4 w-4 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>
                            For large documents, this controls how many pages are processed together. 
                            Smaller chunks (1-5) provide focused extraction but may miss connections. 
                            Larger chunks (10-20) maintain context better.
                          </p>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                    <Input
                      type="number"
                      min={1}
                      max={100}
                      value={maxPagesPerChunk}
                      onChange={(e) => setMaxPagesPerChunk(parseInt(e.target.value) || 10)}
                    />
                  </div>

                  <Separator />

                  {/* Processing Options */}
                  <div className="space-y-3">
                    <TierControls
                      idPrefix="edit"
                      options={processingOptions}
                      onChange={setProcessingOptions}
                    />

                    {!isProcessingValid && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="flex items-center gap-2 text-destructive text-sm"
                      >
                        <AlertCircle className="h-4 w-4" />
                        You must enable at least OCR or GPT Vision
                      </motion.div>
                    )}

                    {/* Cost Indicator */}
                    <div className="flex items-center gap-2 text-sm">
                      <Sparkles className="h-4 w-4 text-yellow-500" />
                      <span className="text-muted-foreground">
                        {enabledSteps <= 1 && "Cost Optimized - Fastest processing"}
                        {enabledSteps === 2 && "Balanced - Good features/cost ratio"}
                        {enabledSteps >= 3 && "Full Processing - Most comprehensive"}
                      </span>
                    </div>
                  </div>

                  <Button 
                    onClick={handleSaveConfiguration} 
                    disabled={isSaving}
                    className="w-full"
                  >
                    {isSaving ? (
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    ) : (
                      <Save className="h-4 w-4 mr-2" />
                    )}
                    Save Configuration
                  </Button>
                </motion.div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right Column - File Upload & New Dataset */}
        <div className="space-y-6">
          {/* File Upload */}
          <Card>
            <CardHeader>
              <CardTitle>Upload Files</CardTitle>
              <CardDescription>
                Upload documents to process with the selected dataset configuration
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Drop Zone */}
              <div 
                className="border-2 border-dashed rounded-lg p-8 text-center hover:border-primary/50 transition-colors cursor-pointer"
                onClick={() => document.getElementById('file-upload')?.click()}
              >
                <input
                  id="file-upload"
                  type="file"
                  multiple
                  accept=".pdf,.pptx,.docx,.xlsx,.jpeg,.jpg,.png,.bmp,.tiff,.heif,.html"
                  onChange={handleFileChange}
                  className="hidden"
                />
                <Upload className="h-10 w-10 mx-auto text-muted-foreground mb-4" />
                <p className="text-sm font-medium">
                  Click to upload or drag and drop
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  PDF, PPTX, DOCX, XLSX, Images (JPG, PNG, TIFF, etc.)
                </p>
              </div>

              {/* Selected Files */}
              <AnimatePresence>
                {files.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="space-y-2"
                  >
                    <Label className="text-sm text-muted-foreground">
                      {files.length} file(s) selected
                    </Label>
                    <div className="max-h-[200px] overflow-y-auto space-y-2">
                      {files.map((file, index) => (
                        <motion.div
                          key={`${file.name}-${index}`}
                          initial={{ opacity: 0, x: -10 }}
                          animate={{ opacity: 1, x: 0 }}
                          exit={{ opacity: 0, x: 10 }}
                          className="flex items-center justify-between p-2 bg-muted rounded-md"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <FileText className="h-4 w-4 flex-shrink-0" />
                            <span className="text-sm truncate">{file.name}</span>
                            <Badge variant="secondary" className="text-xs">
                              {(file.size / 1024 / 1024).toFixed(2)} MB
                            </Badge>
                          </div>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            onClick={() => removeFile(index)}
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        </motion.div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Upload Progress */}
              {isUploading && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="space-y-2"
                >
                  <div className="flex items-center justify-between text-sm">
                    <span>Uploading...</span>
                    <span>{Math.round(uploadProgress)}%</span>
                  </div>
                  <Progress value={uploadProgress} />
                </motion.div>
              )}

              {/* Upload Button */}
              <Button
                onClick={handleUpload}
                disabled={files.length === 0 || !selectedDataset || isUploading || !isProcessingValid}
                className="w-full"
              >
                {isUploading ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Upload className="h-4 w-4 mr-2" />
                )}
                Upload & Process
              </Button>
            </CardContent>
          </Card>

          {/* Processing Options Help */}
          <Accordion type="single" collapsible>
            <AccordionItem value="help" className="border rounded-lg">
              <AccordionTrigger className="px-4">
                <div className="flex items-center gap-2">
                  <HelpCircle className="h-4 w-4" />
                  Processing Options Help
                </div>
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                <div className="space-y-4 text-sm">
                  <div>
                    <strong className="flex items-center gap-2">
                      <FileText className="h-4 w-4" /> OCR Text Extraction
                    </strong>
                    <p className="text-muted-foreground mt-1">
                      Run Document Intelligence to extract text and send to GPT for analysis. 
                      Essential for text-heavy documents.
                    </p>
                  </div>
                  
                  <div>
                    <strong className="flex items-center gap-2">
                      <Eye className="h-4 w-4" /> GPT Vision
                    </strong>
                    <p className="text-muted-foreground mt-1">
                      Send document images to GPT for visual understanding. 
                      Useful for layouts, charts, and visual elements.
                    </p>
                  </div>

                  <div>
                    <strong className="flex items-center gap-2">
                      <FileSearch className="h-4 w-4" /> Data Evaluation
                    </strong>
                    <p className="text-muted-foreground mt-1">
                      Additional GPT call to validate extracted data. 
                      Works best with reasoning models like O1.
                    </p>
                  </div>

                  <div>
                    <strong className="flex items-center gap-2">
                      <ClipboardCheck className="h-4 w-4" /> Summary
                    </strong>
                    <p className="text-muted-foreground mt-1">
                      Generate document summary with key topics and insights.
                    </p>
                  </div>

                  <Separator />

                  <div className="text-muted-foreground">
                    <p><strong>Note:</strong> At least one of OCR or GPT Vision must be enabled.</p>
                    <p className="mt-2">Each enabled option adds processing time and API costs.</p>
                  </div>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>
      </div>
    </PageContainer>
  )
}
