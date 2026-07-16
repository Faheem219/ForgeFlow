import { apiFetch } from '../../lib/apiClient';
import type {
  ArtifactDetail,
  ArtifactDto,
  DesignImageRef,
  DesignProviderInfo,
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

/** Design versions, oldest → newest (the artifact endpoint sorts ascending). */
export function listDesignVersions(projectId: string): Promise<ArtifactDto[]> {
  return apiFetch<ArtifactDto[]>(`/projects/${projectId}/artifacts?stage=design&type=design`);
}

export function getDesignArtifact(artifactId: string): Promise<ArtifactDetail> {
  return apiFetch<ArtifactDetail>(`/artifacts/${artifactId}`);
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
