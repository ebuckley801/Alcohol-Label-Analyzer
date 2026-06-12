import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Download, Loader2, Trash2, X } from "lucide-react";
import { LabelReviewResponse, reviewLabelImage } from "./api/client";
import { ThemeProvider } from "./contexts/ThemeContext";
import { ThemeToggle } from "./components/ThemeToggle";
import { Button } from "./components/Button";
import { Badge } from "./components/Badge";
import { Alert } from "./components/Alert";
import { StatsCard } from "./components/StatsCard";
import { prepareExportItems, resultsToCSV, downloadCSV } from "./utils/csvExport";

type ExpectedFields = {
  expected_brand_name: string;
  expected_alcohol_percentage: string;
  expected_origin_country: string;
};

type QueueStatus = "queued" | "processing" | "complete" | "failed";

type QueueItem = {
  id: string;
  file: File;
  previewUrl: string;
  status: QueueStatus;
  result: LabelReviewResponse | null;
  error: string | null;
};

const QUALITY_WARNING_MESSAGE_PREFIX = "Image quality may be poor";

const defaultExpectedFields: ExpectedFields = {
  expected_brand_name: "",
  expected_alcohol_percentage: "",
  expected_origin_country: "",
};

function AppContent() {
  const [expectedFields, setExpectedFields] = useState<ExpectedFields>(defaultExpectedFields);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [error, setError] = useState<string>("");
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const queueRef = useRef<QueueItem[]>([]);

  const queueSummary = useMemo(() => {
    const total = queue.length;
    const complete = queue.filter((item) => item.status === "complete").length;
    const failed = queue.filter((item) => item.status === "failed").length;
    const pending = queue.filter(
      (item) => item.status === "queued" || item.status === "processing"
    ).length;

    return { total, complete, failed, pending };
  }, [queue]);

  const activeItems = useMemo(
    () => queue.filter((item) => item.status === "queued" || item.status === "processing"),
    [queue]
  );

  const completedItems = useMemo(
    () => queue.filter((item) => item.status === "complete" && item.result !== null),
    [queue]
  );

  const completedWithQualityWarning = useMemo(
    () =>
      completedItems.filter((item) =>
        item.result?.compliance.issues.some((issue) =>
          issue.toLowerCase().startsWith(QUALITY_WARNING_MESSAGE_PREFIX.toLowerCase())
        )
      ),
    [completedItems]
  );

  const completedWithoutQualityWarning = useMemo(
    () =>
      completedItems.filter(
        (item) =>
          !item.result?.compliance.issues.some((issue) =>
            issue.toLowerCase().startsWith(QUALITY_WARNING_MESSAGE_PREFIX.toLowerCase())
          )
      ),
    [completedItems]
  );

  const failedItems = useMemo(() => queue.filter((item) => item.status === "failed"), [queue]);

  useEffect(() => {
    queueRef.current = queue;
  }, [queue]);

  useEffect(() => {
    return () => {
      queueRef.current.forEach((item) => {
        URL.revokeObjectURL(item.previewUrl);
      });
    };
  }, []);

  function onFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) {
      return;
    }

    const acceptedFiles = files.filter((file) => file.type.startsWith("image/"));
    const rejectedCount = files.length - acceptedFiles.length;

    const newItems: QueueItem[] = acceptedFiles.map((file) => ({
      id: `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      previewUrl: URL.createObjectURL(file),
      status: "queued",
      result: null,
      error: null,
    }));

    setQueue((current) => [...current, ...newItems]);
    setError(
      rejectedCount > 0
        ? `${rejectedCount} file(s) skipped because they were not recognized as images.`
        : ""
    );

    event.target.value = "";
  }

  function removeQueueItem(id: string): void {
    if (isProcessing) {
      return;
    }
    setQueue((current) => {
      const target = current.find((item) => item.id === id);
      if (target) {
        URL.revokeObjectURL(target.previewUrl);
      }
      return current.filter((item) => item.id !== id);
    });
  }

  function clearQueue(): void {
    if (isProcessing) {
      return;
    }
    queue.forEach((item) => {
      URL.revokeObjectURL(item.previewUrl);
    });
    setQueue([]);
    setError("");
  }

  function renderResultCard(item: QueueItem) {
    if (!item.result) {
      return <></>;
    }

    const hasQualityWarning = item.result.compliance.issues.some((issue) =>
      issue.toLowerCase().startsWith(QUALITY_WARNING_MESSAGE_PREFIX.toLowerCase())
    );

    return (
      <article key={item.id} className="result-card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-50 truncate">
              {item.file.name}
            </h3>
            <p className="meta">{(item.file.size / 1024).toFixed(1)} KB</p>
          </div>
          <div className="flex gap-2">
            <Badge variant={hasQualityWarning ? "warning" : "success"}>
              {item.result.compliance.is_compliant ? "Compliant" : "Non-Compliant"}
            </Badge>
          </div>
        </div>

        <div className="result-layout">
          <figure className="result-image-wrapper">
            <img src={item.previewUrl} alt={item.file.name} className="result-image" />
          </figure>

          <div className="review-result">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                  Brand
                </p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-50">
                  {item.result.extraction.brand_name || "Not detected"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                  Class/Type
                </p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-50">
                  {item.result.extraction.class_type || "Not detected"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                  Alcohol %
                </p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-50">
                  {item.result.extraction.alcohol_percentage || "Not detected"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                  Net Contents
                </p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-50">
                  {item.result.extraction.net_contents || "Not detected"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                  Origin
                </p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-50">
                  {item.result.extraction.origin_country || "Not detected"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                  Gov Warning
                </p>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-50">
                  {item.result.extraction.has_government_warning ? "Yes" : "No"}
                </p>
              </div>
            </div>

            {item.result.extraction.ai_assisted_fields.length > 0 && (
              <div className="pt-3 border-t border-gray-200 dark:border-gray-700">
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
                  AI Assisted Fields
                </p>
                <div className="flex flex-wrap gap-2">
                  {item.result.extraction.ai_assisted_fields.map((field) => (
                    <Badge key={field} variant="info">
                      {field}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {item.result.compliance.issues.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
                  Compliance Issues
                </p>
                <ul>
                  {item.result.compliance.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              </div>
            )}

            {item.result.compliance.issues.length === 0 && (
              <div className="rounded-lg bg-green-50 p-3 text-sm text-green-700 dark:bg-green-900/20 dark:text-green-200">
                ✓ No compliance issues.
              </div>
            )}
          </div>
        </div>

        <Button
          variant="secondary"
          size="sm"
          onClick={() => removeQueueItem(item.id)}
          disabled={isProcessing}
        >
          <Trash2 className="w-4 h-4" />
          Remove
        </Button>
      </article>
    );
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    if (queue.length === 0) {
      setError("Add at least one label image before running review.");
      return;
    }

    setError("");
    setIsProcessing(true);

    const parsedAlcohol = expectedFields.expected_alcohol_percentage
      ? Number(expectedFields.expected_alcohol_percentage)
      : undefined;

    const normalizedExpectedAlcohol =
      parsedAlcohol !== undefined && Number.isFinite(parsedAlcohol) ? parsedAlcohol : undefined;

    for (const queuedItem of queue) {
      setQueue((current) =>
        current.map((item) =>
          item.id === queuedItem.id ? { ...item, status: "processing", error: null } : item
        )
      );

      try {
        const response = await reviewLabelImage({
          image: queuedItem.file,
          expected_brand_name: expectedFields.expected_brand_name || undefined,
          expected_alcohol_percentage: normalizedExpectedAlcohol,
          expected_origin_country: expectedFields.expected_origin_country || undefined,
        });

        setQueue((current) =>
          current.map((item) =>
            item.id === queuedItem.id
              ? { ...item, status: "complete", result: response, error: null }
              : item
          )
        );
      } catch (err) {
        const message = err instanceof Error ? err.message : "Unknown error";
        setQueue((current) =>
          current.map((item) =>
            item.id === queuedItem.id
              ? { ...item, status: "failed", result: null, error: message }
              : item
          )
        );
      }
    }

    setIsProcessing(false);
  }

  return (
    <main className="page">
      <section className="panel">
        <div className="header mb-6">
          <div className="header-content">
            <h1>🍷 Label Review Queue</h1>
            <p className="subtitle mt-2">
              Upload alcohol labels for OCR-based compliance verification and extraction
            </p>
          </div>
          <ThemeToggle />
        </div>

        <form onSubmit={onSubmit} className="form">
          {/* File Upload */}
          <div>
            <label htmlFor="file-input" className="uploader">
              <div className="uploader-text">📸 Click to upload or drag and drop</div>
              <div className="uploader-hint">PNG, JPG, GIF up to 10MB</div>
              <input
                id="file-input"
                type="file"
                accept="image/*"
                multiple
                onChange={onFileChange}
                disabled={isProcessing}
              />
            </label>
          </div>

          {/* Expected Fields */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="form-group">
              <label htmlFor="brand">Expected Brand Name (optional)</label>
              <input
                id="brand"
                type="text"
                placeholder="e.g., Château Margaux"
                value={expectedFields.expected_brand_name}
                onChange={(event) =>
                  setExpectedFields((prev) => ({
                    ...prev,
                    expected_brand_name: event.target.value,
                  }))
                }
                disabled={isProcessing}
              />
            </div>

            <div className="form-group">
              <label htmlFor="alcohol">Expected Alcohol % (optional)</label>
              <input
                id="alcohol"
                type="number"
                placeholder="e.g., 13.5"
                min={0}
                max={100}
                step="0.1"
                value={expectedFields.expected_alcohol_percentage}
                onChange={(event) =>
                  setExpectedFields((prev) => ({
                    ...prev,
                    expected_alcohol_percentage: event.target.value,
                  }))
                }
                disabled={isProcessing}
              />
            </div>

            <div className="form-group">
              <label htmlFor="origin">Expected Origin Country (optional)</label>
              <input
                id="origin"
                type="text"
                placeholder="e.g., France"
                value={expectedFields.expected_origin_country}
                onChange={(event) =>
                  setExpectedFields((prev) => ({
                    ...prev,
                    expected_origin_country: event.target.value,
                  }))
                }
                disabled={isProcessing}
              />
            </div>
          </div>

          {/* Action Buttons */}
          <div className="actions pt-2">
            <Button
              type="submit"
              disabled={isProcessing || queue.length === 0}
            >
              {isProcessing ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Reviewing...
                </>
              ) : (
                "Run Review Queue"
              )}
            </Button>

            <Button
              type="button"
              variant="secondary"
              onClick={clearQueue}
              disabled={isProcessing}
            >
              <Trash2 className="w-4 h-4" />
              Clear Queue
            </Button>

            {completedItems.length > 0 && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  const exportItems = prepareExportItems(queue);
                  const csv = resultsToCSV(exportItems);
                  downloadCSV(csv, `label-verification-${new Date().toISOString().split("T")[0]}.csv`);
                }}
              >
                <Download className="w-4 h-4" />
                Export Results to CSV
              </Button>
            )}
          </div>
        </form>

        {/* Error Alert */}
        {error && (
          <Alert
            type="error"
            message={error}
            onDismiss={() => setError("")}
          />
        )}

        {/* Queue Summary */}
        {queue.length > 0 && (
          <div className="summary">
            <StatsCard label="Total" value={queueSummary.total} color="blue" />
            <StatsCard label="Completed" value={queueSummary.complete} color="green" />
            <StatsCard label="Failed" value={queueSummary.failed} color="red" />
            <StatsCard label="Pending" value={queueSummary.pending} color="yellow" />
          </div>
        )}

        {/* Active Queue */}
        {activeItems.length > 0 && (
          <section className="queue-section">
            <h2 className="queue-section-title">
              ⏳ Active Queue ({activeItems.length})
            </h2>
            <div className="queue-grid">
              {activeItems.map((item) => (
                <article key={item.id} className="queue-card">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1">
                      <h3 className="queue-card-filename">{item.file.name}</h3>
                      <p className="meta">{(item.file.size / 1024).toFixed(1)} KB</p>
                    </div>
                    <Badge
                      variant={item.status === "processing" ? "warning" : "info"}
                    >
                      {item.status === "processing" && (
                        <Loader2 className="w-3 h-3 animate-spin mr-1" />
                      )}
                      {item.status}
                    </Badge>
                  </div>

                  <div className="result-image-wrapper h-24">
                    <img
                      src={item.previewUrl}
                      alt={item.file.name}
                      className="result-image"
                    />
                  </div>

                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => removeQueueItem(item.id)}
                    disabled={isProcessing}
                  >
                    <X className="w-4 h-4" />
                  </Button>
                </article>
              ))}
            </div>
          </section>
        )}

        {/* Failed Items */}
        {failedItems.length > 0 && (
          <section className="queue-section">
            <h2 className="queue-section-title">
              ❌ Failed Items ({failedItems.length})
            </h2>
            <div className="queue-grid">
              {failedItems.map((item) => (
                <article key={item.id} className="queue-card border-red-200 dark:border-red-900">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1">
                      <h3 className="queue-card-filename">{item.file.name}</h3>
                      <p className="meta">{(item.file.size / 1024).toFixed(1)} KB</p>
                    </div>
                    <Badge variant="danger">Failed</Badge>
                  </div>

                  {item.error && (
                    <Alert
                      type="error"
                      message={item.error}
                    />
                  )}

                  <div className="result-image-wrapper h-24">
                    <img
                      src={item.previewUrl}
                      alt={item.file.name}
                      className="result-image"
                    />
                  </div>

                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => removeQueueItem(item.id)}
                    disabled={isProcessing}
                  >
                    <Trash2 className="w-4 h-4" />
                    Remove
                  </Button>
                </article>
              ))}
            </div>
          </section>
        )}

        {/* Completed Results - No Warning */}
        {completedWithoutQualityWarning.length > 0 && (
          <section className="queue-section">
            <h2 className="queue-section-title">
              ✅ Verified Labels ({completedWithoutQualityWarning.length})
            </h2>
            <div className="space-y-4">
              {completedWithoutQualityWarning.map(renderResultCard)}
            </div>
          </section>
        )}

        {/* Completed Results - With Warning */}
        {completedWithQualityWarning.length > 0 && (
          <section className="queue-section">
            <h2 className="queue-section-title">
              ⚠️ Quality Concerns ({completedWithQualityWarning.length})
            </h2>
            <p className="subtitle mb-4">
              These labels were processed but may have quality issues. Please review carefully.
            </p>
            <div className="space-y-4">
              {completedWithQualityWarning.map(renderResultCard)}
            </div>
          </section>
        )}

        {/* Empty State */}
        {queue.length === 0 && !error && (
          <div className="mt-12 text-center">
            <p className="text-lg text-gray-500 dark:text-gray-400">
              👆 Start by uploading some label images
            </p>
          </div>
        )}
      </section>
    </main>
  );
}

function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

export default App;
