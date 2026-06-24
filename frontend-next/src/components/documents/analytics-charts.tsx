"use client"

import * as React from "react"
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  LineChart,
  Line,
} from "recharts"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { Cost } from "@/lib/api-client"

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

interface AnalyticsChartsProps {
  documents: ProcessedDocument[]
}

const COLORS = {
  completed: "#22c55e",
  processing: "#eab308",
  failed: "#ef4444",
  flagged: "#f97316",
}

const PIE_COLORS = ["#22c55e", "#eab308", "#ef4444"]

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

export function AnalyticsCharts({ documents }: AnalyticsChartsProps) {
  // Status distribution
  const statusData = React.useMemo(() => {
    const counts = {
      completed: documents.filter((d) => d.status === "completed").length,
      processing: documents.filter((d) => d.status === "processing").length,
      failed: documents.filter((d) => d.status === "failed").length,
    }
    return [
      { name: "Completed", value: counts.completed, color: COLORS.completed },
      { name: "Processing", value: counts.processing, color: COLORS.processing },
      { name: "Failed", value: counts.failed, color: COLORS.failed },
    ].filter((d) => d.value > 0)
  }, [documents])

  // Documents by dataset
  const datasetData = React.useMemo(() => {
    const datasetCounts: Record<string, { completed: number; processing: number; failed: number }> = {}
    documents.forEach((doc) => {
      if (!datasetCounts[doc.dataset]) {
        datasetCounts[doc.dataset] = { completed: 0, processing: 0, failed: 0 }
      }
      datasetCounts[doc.dataset][doc.status]++
    })
    return Object.entries(datasetCounts).map(([name, counts]) => ({
      name,
      ...counts,
    }))
  }, [documents])

  // Processing time distribution
  const processingTimeData = React.useMemo(() => {
    return documents
      .filter((d) => d.totalTime !== undefined && d.totalTime > 0)
      .map((d) => ({
        fileName: d.fileName.slice(0, 20),
        time: Math.round(d.totalTime || 0),
        status: d.status,
      }))
      .sort((a, b) => b.time - a.time)
      .slice(0, 20)
  }, [documents])

  // Processing over time
  const timelineData = React.useMemo(() => {
    const byDate: Record<string, { date: string; count: number; completed: number; failed: number }> = {}
    documents.forEach((doc) => {
      const dateStr = doc.timestamp.toISOString().split("T")[0]
      if (!byDate[dateStr]) {
        byDate[dateStr] = { date: dateStr, count: 0, completed: 0, failed: 0 }
      }
      byDate[dateStr].count++
      if (doc.status === "completed") byDate[dateStr].completed++
      if (doc.status === "failed") byDate[dateStr].failed++
    })
    return Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date))
  }, [documents])

  // Pages vs Processing Time scatter
  const scatterData = React.useMemo(() => {
    return documents
      .filter((d) => d.pages && d.totalTime)
      .map((d) => ({
        pages: d.pages,
        time: d.totalTime,
        fileName: d.fileName,
      }))
  }, [documents])

  const costDocuments = React.useMemo(() => {
    return documents.filter((doc) => doc.cost)
  }, [documents])

  const costByTierData = React.useMemo(() => {
    const byTier: Record<string, { name: string; totalCost: number; avgPerPage: number; count: number; pages: number }> = {}
    costDocuments.forEach((doc) => {
      const tier = doc.tier || "unknown"
      if (!byTier[tier]) {
        byTier[tier] = { name: tier, totalCost: 0, avgPerPage: 0, count: 0, pages: 0 }
      }
      byTier[tier].totalCost += doc.cost?.total_usd || 0
      byTier[tier].pages += doc.pages || 0
      byTier[tier].count++
    })
    return Object.values(byTier).map((tier) => ({
      ...tier,
      avgPerPage: tier.pages > 0 ? tier.totalCost / tier.pages : 0,
    }))
  }, [costDocuments])

  const costByDatasetData = React.useMemo(() => {
    const byDataset: Record<string, { name: string; totalCost: number; avgPerPage: number; count: number; pages: number }> = {}
    costDocuments.forEach((doc) => {
      if (!byDataset[doc.dataset]) {
        byDataset[doc.dataset] = { name: doc.dataset, totalCost: 0, avgPerPage: 0, count: 0, pages: 0 }
      }
      byDataset[doc.dataset].totalCost += doc.cost?.total_usd || 0
      byDataset[doc.dataset].pages += doc.pages || 0
      byDataset[doc.dataset].count++
    })
    return Object.values(byDataset)
      .map((dataset) => ({
        ...dataset,
        avgPerPage: dataset.pages > 0 ? dataset.totalCost / dataset.pages : 0,
      }))
      .sort((a, b) => b.totalCost - a.totalCost)
  }, [costDocuments])

  const costTimelineData = React.useMemo(() => {
    const byDate: Record<string, { date: string; totalCost: number; documents: number }> = {}
    costDocuments.forEach((doc) => {
      const dateStr = doc.timestamp.toISOString().split("T")[0]
      if (!byDate[dateStr]) {
        byDate[dateStr] = { date: dateStr, totalCost: 0, documents: 0 }
      }
      byDate[dateStr].totalCost += doc.cost?.total_usd || 0
      byDate[dateStr].documents++
    })
    return Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date))
  }, [costDocuments])

  const failureFlaggedData = React.useMemo(() => {
    const byDataset: Record<string, { name: string; total: number; failed: number; flagged: number }> = {}
    documents.forEach((doc) => {
      if (!byDataset[doc.dataset]) {
        byDataset[doc.dataset] = { name: doc.dataset, total: 0, failed: 0, flagged: 0 }
      }
      byDataset[doc.dataset].total++
      if (doc.status === "failed") byDataset[doc.dataset].failed++
      if (doc.flagged) byDataset[doc.dataset].flagged++
    })
    return Object.values(byDataset).map((dataset) => ({
      name: dataset.name,
      failureRate: dataset.total > 0 ? (dataset.failed / dataset.total) * 100 : 0,
      flaggedRate: dataset.total > 0 ? (dataset.flagged / dataset.total) * 100 : 0,
      failed: dataset.failed,
      flagged: dataset.flagged,
      total: dataset.total,
    }))
  }, [documents])

  // Stats summary
  const stats = React.useMemo(() => {
    const completedDocs = documents.filter((d) => d.status === "completed")
    const timesWithValues = completedDocs.filter((d) => d.totalTime).map((d) => d.totalTime!)
    const avgTime = timesWithValues.length > 0
      ? timesWithValues.reduce((a, b) => a + b, 0) / timesWithValues.length
      : 0
    const totalPages = documents.reduce((sum, d) => sum + (d.pages || 0), 0)
    const successRate = documents.length > 0
      ? (completedDocs.length / documents.length) * 100
      : 0
    const totalCost = costDocuments.reduce((sum, doc) => sum + (doc.cost?.total_usd || 0), 0)
    const costPages = costDocuments.reduce((sum, doc) => sum + (doc.pages || 0), 0)
    const flaggedDocs = documents.filter((doc) => doc.flagged).length

    return {
      totalDocs: documents.length,
      avgTime: avgTime.toFixed(1),
      totalPages,
      successRate: successRate.toFixed(1),
      totalCost,
      avgCostPerPage: costPages > 0 ? totalCost / costPages : 0,
      costDocumentCount: costDocuments.length,
      flaggedDocs,
    }
  }, [documents, costDocuments])

  if (documents.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-muted-foreground">
          No data available for analytics
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Total Documents</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.totalDocs}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Avg Processing Time</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.avgTime}s</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Total Cost</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatUsd(stats.totalCost)}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {stats.costDocumentCount} docs with cost data
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Avg Cost/Page</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatUsd(stats.avgCostPerPage)}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {stats.flaggedDocs} flagged • {stats.successRate}% success
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Charts */}
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="datasets">By Dataset</TabsTrigger>
          <TabsTrigger value="performance">Performance</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="costs">Costs</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {/* Status Pie Chart */}
            <Card>
              <CardHeader>
                <CardTitle>Processing Status</CardTitle>
                <CardDescription>Distribution of document statuses</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-[300px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={statusData}
                        cx="50%"
                        cy="50%"
                        labelLine={false}
                        outerRadius={100}
                        fill="#8884d8"
                        dataKey="value"
                        label={({ name, value, percent }) =>
                          `${name}: ${value} (${((percent || 0) * 100).toFixed(0)}%)`
                        }
                      >
                        {statusData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>

            {/* Processing Time Histogram */}
            <Card>
              <CardHeader>
                <CardTitle>Processing Time</CardTitle>
                <CardDescription>Time taken per document (top 20)</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-[300px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={processingTimeData} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis type="number" unit="s" />
                      <YAxis dataKey="fileName" type="category" width={100} tick={{ fontSize: 10 }} />
                      <Tooltip
                        formatter={(value: number) => [`${value}s`, "Processing Time"]}
                      />
                      <Bar
                        dataKey="time"
                        fill="#3b82f6"
                        radius={[0, 4, 4, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="datasets">
          <Card>
            <CardHeader>
              <CardTitle>Documents by Dataset</CardTitle>
              <CardDescription>Status breakdown per dataset</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[400px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={datasetData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="name" />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="completed" stackId="a" fill={COLORS.completed} name="Completed" />
                    <Bar dataKey="processing" stackId="a" fill={COLORS.processing} name="Processing" />
                    <Bar dataKey="failed" stackId="a" fill={COLORS.failed} name="Failed" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="performance">
          <Card>
            <CardHeader>
              <CardTitle>Pages vs Processing Time</CardTitle>
              <CardDescription>Correlation between document size and processing time</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[400px]">
                {scatterData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="pages" name="Pages" unit=" pages" />
                      <YAxis dataKey="time" name="Time" unit="s" />
                      <Tooltip
                        cursor={{ strokeDasharray: "3 3" }}
                        formatter={(value: number, name: string) => [
                          name === "pages" ? `${value} pages` : `${value}s`,
                          name === "pages" ? "Pages" : "Processing Time",
                        ]}
                      />
                      <Scatter name="Documents" data={scatterData} fill="#3b82f6" />
                    </ScatterChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-full flex items-center justify-center text-muted-foreground">
                    Not enough data for correlation analysis
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="timeline">
          <Card>
            <CardHeader>
              <CardTitle>Processing Timeline</CardTitle>
              <CardDescription>Documents processed over time</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[400px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={timelineData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="count"
                      stroke="#3b82f6"
                      strokeWidth={2}
                      name="Total"
                    />
                    <Line
                      type="monotone"
                      dataKey="completed"
                      stroke={COLORS.completed}
                      strokeWidth={2}
                      name="Completed"
                    />
                    <Line
                      type="monotone"
                      dataKey="failed"
                      stroke={COLORS.failed}
                      strokeWidth={2}
                      name="Failed"
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="costs" className="space-y-4">
          {costDocuments.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center text-muted-foreground">
                Cost data not available for the current document set
              </CardContent>
            </Card>
          ) : (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Cost by Tier</CardTitle>
                    <CardDescription>Total cost and average cost/page by processing tier</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="h-[320px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={costByTierData}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="name" />
                          <YAxis tickFormatter={(value: number) => formatUsd(value)} />
                          <Tooltip
                            formatter={(value: number, name: string) => [
                              formatUsd(value),
                              name === "totalCost" ? "Total Cost" : "Avg Cost/Page",
                            ]}
                          />
                          <Legend />
                          <Bar dataKey="totalCost" fill="#8b5cf6" name="Total Cost" radius={[4, 4, 0, 0]} />
                          <Bar dataKey="avgPerPage" fill="#06b6d4" name="Avg Cost/Page" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Cost by Dataset</CardTitle>
                    <CardDescription>Total cost by dataset</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="h-[320px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={costByDatasetData} layout="vertical">
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis type="number" tickFormatter={(value: number) => formatUsd(value)} />
                          <YAxis dataKey="name" type="category" width={110} tick={{ fontSize: 11 }} />
                          <Tooltip
                            formatter={(value: number, name: string) => [
                              formatUsd(value),
                              name === "totalCost" ? "Total Cost" : "Avg Cost/Page",
                            ]}
                          />
                          <Legend />
                          <Bar dataKey="totalCost" fill="#8b5cf6" name="Total Cost" radius={[0, 4, 4, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Cost Over Time</CardTitle>
                    <CardDescription>Daily processing cost from loaded documents</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="h-[320px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={costTimelineData}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="date" />
                          <YAxis tickFormatter={(value: number) => formatUsd(value)} />
                          <Tooltip formatter={(value: number) => [formatUsd(value), "Total Cost"]} />
                          <Legend />
                          <Line
                            type="monotone"
                            dataKey="totalCost"
                            stroke="#8b5cf6"
                            strokeWidth={2}
                            name="Total Cost"
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </>
          )}
          <Card>
            <CardHeader>
              <CardTitle>Failure & Flagged Rate</CardTitle>
              <CardDescription>Percent failed or flagged by dataset</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[320px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={failureFlaggedData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="name" />
                    <YAxis unit="%" />
                    <Tooltip formatter={(value: number) => [`${value.toFixed(1)}%`, "Rate"]} />
                    <Legend />
                    <Bar dataKey="failureRate" fill={COLORS.failed} name="Failure Rate" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="flaggedRate" fill={COLORS.flagged} name="Flagged Rate" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
