/** Browser-facing v1 contract. Backend classes and file formats stay in build.py. */
export async function readPayload<T>(response: Response): Promise<T> {
  const payload: unknown = await response.json();
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('网页数据包格式无效');
  }
  const data = payload as Record<string, unknown>;
  if ((data.schema_version ?? 1) !== 1) {
    throw new Error('网页数据版本不兼容，请更新网页与数据包');
  }
  return payload as T;
}
