export type VerificationRequest = {
  brand_name: string;
  alcohol_percentage: number;
  has_government_warning: boolean;
  origin_country: string;
};

export type VerificationResponse = {
  is_valid: boolean;
  confidence_score: number;
  issues: string[];
  normalized_brand_name: string;
};

export type LabelReviewExtraction = {
  brand_name: string;
  class_type: string | null;
  alcohol_percentage: number | null;
  net_contents: string | null;
  origin_country: string | null;
  has_government_warning: boolean;
  government_warning_text: string | null;
  raw_text: string | null;
};

export type LabelReviewCompliance = {
  is_compliant: boolean;
  issues: string[];
};

export type LabelReviewResponse = {
  extraction: LabelReviewExtraction;
  compliance: LabelReviewCompliance;
};

export type LabelReviewRequest = {
  image: File;
  expected_brand_name?: string;
  expected_alcohol_percentage?: number;
  expected_origin_country?: string;
};

const RETRY_DELAYS_MS = [250, 600];

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export async function verifyLabel(
  payload: VerificationRequest,
  fetchImpl: typeof fetch = fetch
): Promise<VerificationResponse> {
  const attempts = RETRY_DELAYS_MS.length + 1;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetchImpl("/api/v1/verify", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        if (response.status >= 500 && attempt < attempts - 1) {
          await wait(RETRY_DELAYS_MS[attempt]);
          continue;
        }

        throw new Error(`Verification failed with status ${response.status}`);
      }

      const body = (await response.json()) as VerificationResponse;
      return body;
    } catch (error) {
      if (attempt < attempts - 1) {
        await wait(RETRY_DELAYS_MS[attempt]);
        continue;
      }
      throw error;
    }
  }

  throw new Error("Verification failed unexpectedly");
}

export async function reviewLabelImage(
  payload: LabelReviewRequest,
  fetchImpl: typeof fetch = fetch
): Promise<LabelReviewResponse> {
  const attempts = RETRY_DELAYS_MS.length + 1;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const formData = new FormData();
      formData.append("image", payload.image);

      if (payload.expected_brand_name) {
        formData.append("expected_brand_name", payload.expected_brand_name);
      }
      if (payload.expected_alcohol_percentage !== undefined) {
        formData.append(
          "expected_alcohol_percentage",
          String(payload.expected_alcohol_percentage)
        );
      }
      if (payload.expected_origin_country) {
        formData.append("expected_origin_country", payload.expected_origin_country);
      }

      const response = await fetchImpl("/api/v1/review", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        if (response.status >= 500 && attempt < attempts - 1) {
          await wait(RETRY_DELAYS_MS[attempt]);
          continue;
        }

        throw new Error(`Label review failed with status ${response.status}`);
      }

      return (await response.json()) as LabelReviewResponse;
    } catch (error) {
      if (attempt < attempts - 1) {
        await wait(RETRY_DELAYS_MS[attempt]);
        continue;
      }
      throw error;
    }
  }

  throw new Error("Label review failed unexpectedly");
}
