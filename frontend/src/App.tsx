import { ChangeEvent, DragEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Download, Loader2, Trash2, X } from "lucide-react";
import { LabelReviewResponse, reviewLabelImage } from "./api/client";
import type { IssueSeverity } from "./api/client";
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

function severityBadgeVariant(severity: IssueSeverity): "danger" | "warning" | "info" {
  if (severity === "error") {
    return "danger";
  }
  if (severity === "warning") {
    return "warning";
  }
  return "info";
}

// Number of labels reviewed in parallel. Bounded so a 300-label batch keeps a
// steady throughput without overwhelming the browser or the backend/Azure quota.
const MAX_CONCURRENT_REVIEWS = 6;

// Matches the "up to 10MB" hint shown in the uploader.
const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024;

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
  const [isCancelling, setIsCancelling] = useState<boolean>(false);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const queueRef = useRef<QueueItem[]>([]);
  const cancelRef = useRef<boolean>(false);
  const abortControllersRef = useRef<Set<AbortController>>(new Set());

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

  function addFiles(files: File[]): void {
    if (files.length === 0) {
      return;
    }

    const images = files.filter((file) => file.type.startsWith("image/"));
    const nonImageCount = files.length - images.length;

    const acceptedFiles = images.filter((file) => file.size <= MAX_FILE_SIZE_BYTES);
    const oversizedCount = images.length - acceptedFiles.length;

    const newItems: QueueItem[] = acceptedFiles.map((file) => ({
      id: `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      previewUrl: URL.createObjectURL(file),
      status: "queued",
      result: null,
      error: null,
    }));

    if (newItems.length > 0) {
      setQueue((current) => [...current, ...newItems]);
    }

    const messages: string[] = [];
    if (nonImageCount > 0) {
      messages.push(`${nonImageCount} file(s) skipped (not images).`);
    }
    if (oversizedCount > 0) {
      messages.push(`${oversizedCount} file(s) skipped (larger than 10MB).`);
    }
    setError(messages.join(" "));
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>): void {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLLabelElement>): void {
    event.preventDefault();
    setIsDragging(false);
    if (isProcessing) {
      return;
    }
    addFiles(Array.from(event.dataTransfer.files ?? []));
  }

  function onDragOver(event: DragEvent<HTMLLabelElement>): void {
    event.preventDefault();
    if (!isProcessing) {
      setIsDragging(true);
    }
  }

  function onDragLeave(event: DragEvent<HTMLLabelElement>): void {
    event.preventDefault();
    setIsDragging(false);
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
            {hasQualityWarning && <Badge variant="warning">Quality</Badge>}
            <Badge variant={item.result.compliance.is_compliant ? "success" : "danger"}>
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
                  {item.result.extraction.alcohol_percentage !== null
                    ? `${item.result.extraction.alcohol_percentage}%`
                    : "Not detected"}
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

            {item.result.compliance.issues_detail.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
                  Compliance Issues
                </p>
                <ul className="space-y-2">
                  {item.result.compliance.issues_detail.map((issue) => (
                    <li
                      key={issue.code}
                      className="rounded-lg border border-gray-200 p-2 text-sm dark:border-gray-700"
                    >
                      <div className="flex items-start gap-2">
                        <Badge variant={severityBadgeVariant(issue.severity)}>
                          {issue.severity.toUpperCase()}
                        </Badge>
                        <span className="text-gray-800 dark:text-gray-100">{issue.message}</span>
                      </div>
                      {issue.citation && (
                        <p className="mt-1 pl-1 text-xs text-gray-500 dark:text-gray-400">
                          {issue.citation}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {item.result.compliance.issues_detail.length === 0 && (
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

    const pendingItems = queueRef.current.filter(
      (item) => item.status === "queued" || item.status === "failed"
    );

    if (pendingItems.length === 0) {
      setError("Add at least one label image before running review.");
      return;
    }

    setError("");
    setIsProcessing(true);
    setIsCancelling(false);
    cancelRef.current = false;

    const parsedAlcohol = expectedFields.expected_alcohol_percentage
      ? Number(expectedFields.expected_alcohol_percentage)
      : undefined;

    const normalizedExpectedAlcohol =
      parsedAlcohol !== undefined && Number.isFinite(parsedAlcohol) ? parsedAlcohol : undefined;

    // Reset previously-failed items back to queued so a re-run retries them.
    setQueue((current) =>
      current.map((item) =>
        item.status === "failed" ? { ...item, status: "queued", error: null } : item
      )
    );

    // Shared cursor consumed by a fixed pool of workers -> bounded concurrency.
    let cursor = 0;

    async function worker(): Promise<void> {
      while (true) {
        if (cancelRef.current) {
          return;
        }

        const index = cursor;
        cursor += 1;
        if (index >= pendingItems.length) {
          return;
        }

        const queuedItem = pendingItems[index];

        setQueue((current) =>
          current.map((item) =>
            item.id === queuedItem.id ? { ...item, status: "processing", error: null } : item
          )
        );

        const controller = new AbortController();
        abortControllersRef.current.add(controller);

        try {
          const response = await reviewLabelImage(
            {
              image: queuedItem.file,
              expected_brand_name: expectedFields.expected_brand_name || undefined,
              expected_alcohol_percentage: normalizedExpectedAlcohol,
              expected_origin_country: expectedFields.expected_origin_country || undefined,
            },
            fetch,
            controller.signal
          );

          setQueue((current) =>
            current.map((item) =>
              item.id === queuedItem.id
                ? { ...item, status: "complete", result: response, error: null }
                : item
            )
          );
        } catch (err) {
          const aborted = err instanceof DOMException && err.name === "AbortError";
          setQueue((current) =>
            current.map((item) =>
              item.id === queuedItem.id
                ? aborted
                  ? { ...item, status: "queued", result: null, error: null }
                  : {
                      ...item,
                      status: "failed",
                      result: null,
                      error: err instanceof Error ? err.message : "Unknown error",
                    }
                : item
            )
          );
        } finally {
          abortControllersRef.current.delete(controller);
        }
      }
    }

    const workerCount = Math.min(MAX_CONCURRENT_REVIEWS, pendingItems.length);
    await Promise.all(Array.from({ length: workerCount }, () => worker()));

    abortControllersRef.current.clear();
    setIsProcessing(false);
    setIsCancelling(false);
    cancelRef.current = false;
  }

  function cancelProcessing(): void {
    if (!isProcessing) {
      return;
    }
    cancelRef.current = true;
    setIsCancelling(true);
    abortControllersRef.current.forEach((controller) => controller.abort());
    abortControllersRef.current.clear();
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
            <label
              htmlFor="file-input"
              className={`uploader${isDragging ? " uploader--dragging" : ""}`}
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
            >
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
              disabled={isProcessing || queueSummary.pending === 0}
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

            {isProcessing && (
              <Button
                type="button"
                variant="secondary"
                onClick={cancelProcessing}
                disabled={isCancelling}
              >
                <X className="w-4 h-4" />
                {isCancelling ? "Cancelling..." : "Cancel"}
              </Button>
            )}

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

        {/* Progress Bar */}
        {queue.length > 0 && (
          <div className="mt-4" aria-live="polite">
            <div className="flex justify-between text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              <span>
                {queueSummary.complete + queueSummary.failed} of {queueSummary.total} processed
                {isProcessing ? ` (${MAX_CONCURRENT_REVIEWS} at a time)` : ""}
              </span>
              <span>
                {queueSummary.total > 0
                  ? Math.round(
                      ((queueSummary.complete + queueSummary.failed) / queueSummary.total) * 100
                    )
                  : 0}
                %
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
              <div
                className="h-full rounded-full bg-blue-500 transition-all duration-300"
                style={{
                  width: `${
                    queueSummary.total > 0
                      ? ((queueSummary.complete + queueSummary.failed) / queueSummary.total) * 100
                      : 0
                  }%`,
                }}
              />
            </div>
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
