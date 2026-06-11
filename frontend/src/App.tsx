import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { LabelReviewResponse, reviewLabelImage } from "./api/client";

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

function App() {
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

    return (
      <article key={item.id} className="queue-card result-card">
        <header className="queue-card-header">
          <h3>{item.file.name}</h3>
          <span className={`status status-${item.status}`}>{item.status}</span>
        </header>

        <p className="meta">{(item.file.size / 1024).toFixed(1)} KB</p>

        <div className="result-layout">
          <figure className="result-image-wrapper">
            <img src={item.previewUrl} alt={item.file.name} className="result-image" />
          </figure>

          <div className="review-result">
            <p>
              <strong>Compliant:</strong> {item.result.compliance.is_compliant ? "Yes" : "No"}
            </p>
            <p>
              <strong>Brand:</strong> {item.result.extraction.brand_name}
            </p>
            <p>
              <strong>Class/Type:</strong> {item.result.extraction.class_type ?? "Not detected"}
            </p>
            <p>
              <strong>Alcohol %:</strong> {item.result.extraction.alcohol_percentage ?? "Not detected"}
            </p>
            <p>
              <strong>Net Contents:</strong> {item.result.extraction.net_contents ?? "Not detected"}
            </p>
            <p>
              <strong>Origin Country:</strong> {item.result.extraction.origin_country ?? "Not detected"}
            </p>
            <p>
              <strong>Gov Warning Found:</strong> {item.result.extraction.has_government_warning ? "Yes" : "No"}
            </p>

            {item.result.compliance.issues.length > 0 ? (
              <ul>
                {item.result.compliance.issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            ) : (
              <p>No compliance issues.</p>
            )}
          </div>
        </div>

        <button
          type="button"
          className="secondary"
          onClick={() => removeQueueItem(item.id)}
          disabled={isProcessing}
        >
          Remove
        </button>
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
        <h1>Label Review Queue</h1>
        <p className="subtitle">
          Upload one or many images, then run OCR-based compliance review for each label.
        </p>

        <form onSubmit={onSubmit} className="form">
          <label className="uploader">
            Add label images
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={onFileChange}
              disabled={isProcessing}
            />
          </label>

          <label>
            Expected brand name (optional)
            <input
              value={expectedFields.expected_brand_name}
              onChange={(event) =>
                setExpectedFields((prev) => ({
                  ...prev,
                  expected_brand_name: event.target.value,
                }))
              }
            />
          </label>

          <label>
            Expected alcohol % (optional)
            <input
              type="number"
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
            />
          </label>

          <label>
            Expected origin country (optional)
            <input
              value={expectedFields.expected_origin_country}
              onChange={(event) =>
                setExpectedFields((prev) => ({
                  ...prev,
                  expected_origin_country: event.target.value,
                }))
              }
            />
          </label>

          <div className="actions">
            <button type="submit" disabled={isProcessing || queue.length === 0}>
              {isProcessing ? "Reviewing queue..." : "Run Review Queue"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={clearQueue}
              disabled={isProcessing}
            >
              Clear Queue
            </button>
          </div>
        </form>

        <section className="summary">
          <h2>Queue Summary</h2>
          <p>
            {queueSummary.total} total, {queueSummary.complete} complete, {queueSummary.failed} failed,
            {" "}
            {queueSummary.pending} pending.
          </p>
        </section>

        {error ? <p className="error">{error}</p> : null}

        {activeItems.length > 0 ? (
          <section>
            <h2>Active Queue</h2>
            <div className="queue-grid">
              {activeItems.map((item) => (
                <article key={item.id} className="queue-card">
                  <header className="queue-card-header">
                    <h3>{item.file.name}</h3>
                    <span className={`status status-${item.status}`}>{item.status}</span>
                  </header>

                  <p className="meta">{(item.file.size / 1024).toFixed(1)} KB</p>

                  <div className="result-image-wrapper queue-preview">
                    <img src={item.previewUrl} alt={item.file.name} className="result-image" />
                  </div>

                  <p className="meta">No result yet.</p>

                  <button
                    type="button"
                    className="secondary"
                    onClick={() => removeQueueItem(item.id)}
                    disabled={isProcessing}
                  >
                    Remove
                  </button>
                </article>
              ))}
            </div>
          </section>
        ) : null}

        {failedItems.length > 0 ? (
          <section>
            <h2>Failed Items</h2>
            <div className="queue-grid">
              {failedItems.map((item) => (
                <article key={item.id} className="queue-card">
                  <header className="queue-card-header">
                    <h3>{item.file.name}</h3>
                    <span className={`status status-${item.status}`}>{item.status}</span>
                  </header>

                  <p className="meta">{(item.file.size / 1024).toFixed(1)} KB</p>
                  <div className="result-image-wrapper queue-preview">
                    <img src={item.previewUrl} alt={item.file.name} className="result-image" />
                  </div>
                  {item.error ? <p className="error">{item.error}</p> : null}

                  <button
                    type="button"
                    className="secondary"
                    onClick={() => removeQueueItem(item.id)}
                    disabled={isProcessing}
                  >
                    Remove
                  </button>
                </article>
              ))}
            </div>
          </section>
        ) : null}

        <section>
          <h2>Completed: No Quality Warning ({completedWithoutQualityWarning.length})</h2>
          <div className="queue-grid">
            {completedWithoutQualityWarning.length > 0 ? (
              completedWithoutQualityWarning.map(renderResultCard)
            ) : (
              <p className="meta">No completed results without quality warnings yet.</p>
            )}
          </div>
        </section>

        <section>
          <h2>Completed: With Quality Warning ({completedWithQualityWarning.length})</h2>
          <div className="queue-grid">
            {completedWithQualityWarning.length > 0 ? (
              completedWithQualityWarning.map(renderResultCard)
            ) : (
              <p className="meta">No completed results with quality warnings yet.</p>
            )}
          </div>
        </section>
      </section>
    </main>
  );
}

export default App;
