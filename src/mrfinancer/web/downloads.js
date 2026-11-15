export function downloadFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function requestDownload(url, options, filename) {
  const response = await fetch(url, {method: "POST", ...options});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "Could not export the file. Try again.");
  }
  downloadFile(await response.blob(), filename);
}
