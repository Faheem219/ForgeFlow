import { apiFetch } from '../../lib/apiClient';
import type {
  ArtifactDetail,
  ArtifactDto,
  DesignImageRef,
  DesignProviderInfo,
  DesignQuestionDto,
} from '../../lib/types';

export interface UploadImage {
  filename: string;
  media_type: string;
  data_base64: string;
}

export function uploadDesignImages(
  projectId: string,
  images: UploadImage[],
): Promise<{ images: DesignImageRef[] }> {
  return apiFetch<{ images: DesignImageRef[] }>(`/projects/${projectId}/design/images`, {
    method: 'POST',
    body: JSON.stringify({ images }),
  });
}

export function getDesignProvider(projectId: string): Promise<DesignProviderInfo> {
  return apiFetch<DesignProviderInfo>(`/projects/${projectId}/design/provider`);
}

/**
 * The provider's pending question, or `null` when it isn't waiting on anything. Answering goes
 * through the conductor like any other design turn — the answer is the intent's `message`.
 */
export function getDesignQuestion(projectId: string): Promise<DesignQuestionDto | null> {
  return apiFetch<DesignQuestionDto | null>(`/projects/${projectId}/design/question`);
}

/** Design versions, oldest → newest (the artifact endpoint sorts ascending). */
export function listDesignVersions(projectId: string): Promise<ArtifactDto[]> {
  return apiFetch<ArtifactDto[]>(`/projects/${projectId}/artifacts?stage=design&type=design`);
}

export function getDesignArtifact(artifactId: string): Promise<ArtifactDetail> {
  return apiFetch<ArtifactDetail>(`/artifacts/${artifactId}`);
}

/** A screen that lives in the provider's project (a whole app spans many of them). */
export interface DesignScreenRef {
  ref: string;
  title: string;
  preview_image: string | null;
}

/** Every screen in the provider's project. Empty when the provider cannot enumerate them. */
export function listDesignScreens(projectId: string): Promise<{ screens: DesignScreenRef[] }> {
  return apiFetch<{ screens: DesignScreenRef[] }>(`/projects/${projectId}/design/screens`);
}

/** One screen's markup, fetched on demand when it is picked in the preview. */
export function getDesignScreen(
  projectId: string,
  ref: string,
): Promise<{ ref: string; html: string; css: string }> {
  return apiFetch<{ ref: string; html: string; css: string }>(
    `/projects/${projectId}/design/screen?ref=${encodeURIComponent(ref)}`,
  );
}

/** Read a File as a base64 string (no data: prefix) for the JSON upload endpoint. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      resolve(result.slice(result.indexOf(',') + 1)); // strip "data:<type>;base64,"
    };
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.readAsDataURL(file);
  });
}
