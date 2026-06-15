export interface ExportItem {
  id: string;
  fileName: string;
  fileSizeKb: number;
  status: "complete" | "failed";
  error?: string;
  compliant?: boolean;
  brandName?: string;
  classType?: string | null;
  alcoholPercentage?: number | null;
  netContents?: string | null;
  originCountry?: string | null;
  hasGovernmentWarning?: boolean;
  aiAssistedFields?: string;
  complianceIssues?: string;
}

export function resultsToCSV(items: ExportItem[]): string {
  if (items.length === 0) {
    return "";
  }

  const headers = [
    "File Name",
    "File Size (KB)",
    "Status",
    "Error",
    "Compliant",
    "Brand Name",
    "Class/Type",
    "Alcohol %",
    "Net Contents",
    "Origin Country",
    "Government Warning",
    "AI Assisted Fields",
    "Compliance Issues",
  ];

  const rows = items.map((item) => [
    escapeCSV(item.fileName),
    item.fileSizeKb.toFixed(1),
    item.status,
    item.error ? escapeCSV(item.error) : "",
    item.compliant !== undefined ? (item.compliant ? "Yes" : "No") : "",
    item.brandName ? escapeCSV(item.brandName) : "",
    item.classType ? escapeCSV(item.classType) : "",
    item.alcoholPercentage !== undefined && item.alcoholPercentage !== null ? String(item.alcoholPercentage) : "",
    item.netContents ? escapeCSV(item.netContents) : "",
    item.originCountry ? escapeCSV(item.originCountry) : "",
    item.hasGovernmentWarning !== undefined ? (item.hasGovernmentWarning ? "Yes" : "No") : "",
    item.aiAssistedFields ? escapeCSV(item.aiAssistedFields) : "",
    item.complianceIssues ? escapeCSV(item.complianceIssues) : "",
  ]);

  const csvContent = [
    headers.map(escapeCSV).join(","),
    ...rows.map((row) => row.join(",")),
  ].join("\n");

  return csvContent;
}

function escapeCSV(value: string): string {
  if (!value) return '""';
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

export function downloadCSV(csv: string, filename: string = "label-verification-results.csv"): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);
  link.setAttribute("href", url);
  link.setAttribute("download", filename);
  link.style.visibility = "hidden";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

interface QueueItemForExport {
  id: string;
  file: File;
  status: "queued" | "processing" | "complete" | "failed";
  error: string | null;
  result: {
    compliance: {
      is_compliant: boolean;
      issues: string[];
      issues_detail: {
        code: string;
        message: string;
        severity: "error" | "warning" | "info";
        citation: string | null;
      }[];
    };
    extraction: {
      brand_name: string;
      class_type: string | null;
      alcohol_percentage: number | null;
      net_contents: string | null;
      origin_country: string | null;
      has_government_warning: boolean;
      ai_assisted_fields: string[];
    };
  } | null;
}

export function prepareExportItems(
  queue: QueueItemForExport[]
): ExportItem[] {
  return queue
    .filter((item) => item.status === "complete" || item.status === "failed")
    .map((item) => ({
      id: item.id,
      fileName: item.file.name,
      fileSizeKb: item.file.size / 1024,
      status: item.status as "complete" | "failed",
      error: item.error || undefined,
      compliant: item.result?.compliance.is_compliant,
      brandName: item.result?.extraction.brand_name,
      classType: item.result?.extraction.class_type,
      alcoholPercentage: item.result?.extraction.alcohol_percentage,
      netContents: item.result?.extraction.net_contents,
      originCountry: item.result?.extraction.origin_country,
      hasGovernmentWarning: item.result?.extraction.has_government_warning,
      aiAssistedFields: item.result?.extraction.ai_assisted_fields.join("; "),
      complianceIssues: item.result?.compliance.issues_detail
        .map(
          (issue) =>
            `[${issue.severity.toUpperCase()}] ${issue.message}` +
            (issue.citation ? ` (${issue.citation})` : "")
        )
        .join("; "),
    }));
}
