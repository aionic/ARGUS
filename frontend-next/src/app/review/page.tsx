"use client"

import * as React from "react"
import {
  AlertTriangle,
  CheckCircle2,
  FileWarning,
  Loader2,
  Mail,
  RefreshCw,
  Send,
} from "lucide-react"
import { toast } from "sonner"

import { PageContainer } from "@/components/layout/page-container"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import {
  FlaggedItem,
  generateFlagEmail,
  getFlaggedDocuments,
  sendFlagEmailMock,
} from "@/lib/api-client"
import { formatDate } from "@/lib/utils"

const DEFAULT_PROMPT_TEMPLATE = `Draft a warm, concise, professional email to the document uploader, who is a business user (not a technical specialist). Explain in plain, everyday language that we were unable to process their document reliably.

Summarize the issue(s) using the plain-language reasons provided in the document context. Do NOT use internal codes, field names, technical jargon, raw metric values, or thresholds (for example, never write tokens like "low_ocr_confidence", "laplacian", or "< 0.60") - translate everything into language a non-technical business user understands.

Then propose friendly, concrete next steps: re-scan the document at a higher quality/resolution, ensure good lighting with the page laid flat, and re-upload the corrected file. Keep the tone helpful and reassuring, not blaming.`
const DEFAULT_TO_ADDRESS = "uploader@example.com"

type ComposerProps = {
  document: FlaggedItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onMockSent: (documentId: string) => void
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function StageBadge({ stage }: { stage: FlaggedItem["stage"] }) {
  return (
    <Badge variant={stage === "quality" ? "destructive" : "warning"} className="capitalize">
      {stage}
    </Badge>
  )
}

function EmailSentBadge({ emailSent }: { emailSent?: boolean }) {
  if (emailSent) {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" />
        Sent
      </Badge>
    )
  }

  return <Badge variant="secondary">Not sent</Badge>
}

function FlagEmailComposer({ document, open, onOpenChange, onMockSent }: ComposerProps) {
  const [promptTemplate, setPromptTemplate] = React.useState(DEFAULT_PROMPT_TEMPLATE)
  const [to, setTo] = React.useState(DEFAULT_TO_ADDRESS)
  const [subject, setSubject] = React.useState("")
  const [body, setBody] = React.useState("")
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null)
  const [isGenerating, setIsGenerating] = React.useState(false)
  const [isSending, setIsSending] = React.useState(false)

  React.useEffect(() => {
    if (!open || !document) return

    setPromptTemplate(DEFAULT_PROMPT_TEMPLATE)
    setTo(DEFAULT_TO_ADDRESS)
    setSubject("")
    setBody("")
    setErrorMessage(null)
    setIsGenerating(false)
    setIsSending(false)
  }, [document, open])

  async function handleGenerate() {
    if (!document) return

    setIsGenerating(true)
    setErrorMessage(null)
    try {
      const draft = await generateFlagEmail(
        document.id,
        promptTemplate.trim() || undefined
      )
      setSubject(draft.subject)
      setBody(draft.body)
      if (draft.to) setTo(draft.to)
      toast.success("Draft generated")
    } catch (error) {
      const message = getErrorMessage(error, "Failed to generate draft")
      setErrorMessage(message)
      toast.error("Failed to generate draft", { description: message })
    } finally {
      setIsGenerating(false)
    }
  }

  async function handleSendMock() {
    if (!document) return

    const trimmedTo = to.trim()
    const trimmedSubject = subject.trim()
    const trimmedBody = body.trim()

    if (!trimmedTo || !trimmedSubject || !trimmedBody) {
      toast.error("To, subject, and body are required before mock send")
      return
    }

    setIsSending(true)
    setErrorMessage(null)
    try {
      const response = await sendFlagEmailMock({
        document_id: document.id,
        to: trimmedTo,
        subject: trimmedSubject,
        body: trimmedBody,
      })

      if (!response.success) {
        throw new Error("Mock send did not complete successfully")
      }

      toast.success(`Mock email sent — message_id: ${response.message_id}`)
      onMockSent(document.id)
      onOpenChange(false)
    } catch (error) {
      const message = getErrorMessage(error, "Failed to send mock email")
      setErrorMessage(message)
      toast.error("Failed to send mock email", { description: message })
    } finally {
      setIsSending(false)
    }
  }

  const canSend = Boolean(to.trim() && subject.trim() && body.trim())

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <div className="flex flex-wrap items-center gap-2 pr-8">
            <SheetTitle>Compose flagged document email</SheetTitle>
            <Badge variant="warning">MOCK</Badge>
          </div>
          <SheetDescription>
            {document
              ? `Draft a simulated notification for ${document.filename}.`
              : "Draft a simulated notification for a flagged document."}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 py-4">
          <Alert className="border-yellow-500/50 bg-yellow-50 text-yellow-900 dark:bg-yellow-950/20 dark:text-yellow-100">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Mock send only</AlertTitle>
            <AlertDescription>
              This is a simulated send for review workflow testing — no real email is delivered.
            </AlertDescription>
          </Alert>

          {errorMessage && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Email action failed</AlertTitle>
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          )}

          {document && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Flagged document</CardTitle>
                <CardDescription>{document.dataset}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="font-medium">{document.filename}</div>
                <div className="flex flex-wrap gap-2">
                  <StageBadge stage={document.stage} />
                  {document.reasons.map((reason) => (
                    <Badge key={reason} variant="outline">
                      {reason}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <div className="space-y-2">
            <Label htmlFor="prompt-template">Generation instruction/template</Label>
            <Textarea
              id="prompt-template"
              value={promptTemplate}
              onChange={(event) => setPromptTemplate(event.target.value)}
              rows={5}
              placeholder="Describe how ARGUS should draft the flagged-document email."
            />
            <p className="text-xs text-muted-foreground">
              Edit this instruction before generating to steer the LLM-drafted subject and body.
            </p>
          </div>

          <Button onClick={handleGenerate} disabled={!document || isGenerating}>
            {isGenerating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Mail className="h-4 w-4" />
            )}
            {isGenerating ? "Generating..." : "Generate draft"}
          </Button>

          <div className="space-y-2">
            <Label htmlFor="email-to">To</Label>
            <Input
              id="email-to"
              type="email"
              value={to}
              onChange={(event) => setTo(event.target.value)}
              placeholder={DEFAULT_TO_ADDRESS}
            />
            <p className="text-xs text-muted-foreground">
              Uploader address may be unavailable; this fallback is used for mock send only.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="email-subject">Subject</Label>
            <Input
              id="email-subject"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder="Generate a draft or enter a subject"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="email-body">Body</Label>
            <Textarea
              id="email-body"
              value={body}
              onChange={(event) => setBody(event.target.value)}
              rows={10}
              placeholder="Generate a draft or enter the email body"
            />
          </div>
        </div>

        <SheetFooter className="gap-2 sm:space-x-0">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSending}>
            Cancel
          </Button>
          <Button onClick={handleSendMock} disabled={!document || !canSend || isSending}>
            {isSending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            Send (Mock)
            <Badge variant="secondary" className="ml-1">MOCK</Badge>
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

export default function ReviewPage() {
  const [flaggedItems, setFlaggedItems] = React.useState<FlaggedItem[]>([])
  const [selectedItem, setSelectedItem] = React.useState<FlaggedItem | null>(null)
  const [composerOpen, setComposerOpen] = React.useState(false)
  const [isLoading, setIsLoading] = React.useState(true)
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null)

  async function loadFlaggedDocuments() {
    setIsLoading(true)
    setErrorMessage(null)
    try {
      const items = await getFlaggedDocuments()
      setFlaggedItems(
        [...items].sort(
          (a, b) =>
            new Date(b.flagged_at).getTime() - new Date(a.flagged_at).getTime()
        )
      )
    } catch (error) {
      const message = getErrorMessage(error, "Failed to load flagged documents")
      setErrorMessage(message)
      toast.error("Failed to load flagged documents", { description: message })
    } finally {
      setIsLoading(false)
    }
  }

  React.useEffect(() => {
    loadFlaggedDocuments()
  }, [])

  function openComposer(item: FlaggedItem) {
    setSelectedItem(item)
    setComposerOpen(true)
  }

  function handleComposerOpenChange(open: boolean) {
    setComposerOpen(open)
    if (!open) {
      setSelectedItem(null)
    }
  }

  function handleMockSent(documentId: string) {
    setFlaggedItems((items) =>
      items.map((item) =>
        item.id === documentId ? { ...item, email_sent: true } : item
      )
    )
  }

  const sentCount = flaggedItems.filter((item) => item.email_sent).length
  const pendingCount = flaggedItems.length - sentCount

  return (
    <PageContainer
      title="🚩 Review / Flagged"
      description="Review flagged documents and draft mock uploader notifications."
    >
      <div className="grid gap-4 md:grid-cols-3 mb-6">
        <Card className="border-l-4 border-l-red-500 bg-gradient-to-r from-red-50/50 to-transparent dark:from-red-950/20">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Flagged Documents</CardTitle>
            <FileWarning className="h-4 w-4 text-red-600 dark:text-red-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-700 dark:text-red-400">{flaggedItems.length}</div>
            <p className="text-xs text-muted-foreground mt-1">require review</p>
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-yellow-500 bg-gradient-to-r from-yellow-50/50 to-transparent dark:from-yellow-950/20">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Pending Email</CardTitle>
            <Mail className="h-4 w-4 text-yellow-600 dark:text-yellow-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-yellow-700 dark:text-yellow-400">{pendingCount}</div>
            <p className="text-xs text-muted-foreground mt-1">mock send not recorded</p>
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-green-500 bg-gradient-to-r from-green-50/50 to-transparent dark:from-green-950/20">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Mock Sent</CardTitle>
            <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-700 dark:text-green-400">{sentCount}</div>
            <p className="text-xs text-muted-foreground mt-1">simulated notifications</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Flagged documents</CardTitle>
              <CardDescription>
                Generate and mock-send notification emails for documents flagged during preflight or quality review.
              </CardDescription>
            </div>
            <Button variant="outline" onClick={loadFlaggedDocuments} disabled={isLoading}>
              <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {errorMessage && (
            <Alert variant="destructive" className="mb-4">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Unable to load flagged documents</AlertTitle>
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          )}

          {isLoading ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, index) => (
                <Skeleton key={index} className="h-14 w-full" />
              ))}
            </div>
          ) : flaggedItems.length === 0 ? (
            <div className="flex min-h-[220px] flex-col items-center justify-center rounded-lg border border-dashed p-8 text-center">
              <CheckCircle2 className="mb-4 h-10 w-10 text-green-500" />
              <h3 className="text-lg font-semibold">No flagged documents 🎉</h3>
              <p className="mt-2 max-w-md text-sm text-muted-foreground">
                Documents that fail preflight or quality checks will appear here for review and mock email drafting.
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Filename</TableHead>
                  <TableHead>Dataset</TableHead>
                  <TableHead>Reasons</TableHead>
                  <TableHead>Stage</TableHead>
                  <TableHead>Flagged at</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {flaggedItems.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="max-w-[260px] font-medium">
                      <div className="truncate" title={item.filename}>{item.filename}</div>
                      <div className="truncate text-xs text-muted-foreground" title={item.id}>{item.id}</div>
                    </TableCell>
                    <TableCell>{item.dataset}</TableCell>
                    <TableCell>
                      <div className="flex max-w-[320px] flex-wrap gap-1.5">
                        {item.reasons.length > 0 ? (
                          item.reasons.map((reason) => (
                            <Badge key={`${item.id}-${reason}`} variant="outline">
                              {reason}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-sm text-muted-foreground">No reason provided</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <StageBadge stage={item.stage} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{formatDate(item.flagged_at)}</TableCell>
                    <TableCell>
                      <EmailSentBadge emailSent={item.email_sent} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" onClick={() => openComposer(item)}>
                        <Mail className="h-4 w-4" />
                        Compose email
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <FlagEmailComposer
        document={selectedItem}
        open={composerOpen}
        onOpenChange={handleComposerOpenChange}
        onMockSent={handleMockSent}
      />
    </PageContainer>
  )
}
