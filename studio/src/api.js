export function errorMessage(detail) {
  if (Array.isArray(detail)) return detail.map(x => `${(x.loc || []).join('.')}: ${x.msg}`).join('; ');
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return detail || 'Unbekannter Fehler';
}
export async function request(path, {method='GET',body,...options}={}) {
  const isForm = body instanceof FormData;
  const response = await fetch(`/api${path}`, {method,...options,...(body===undefined?{}:{body:isForm?body:JSON.stringify(body),headers:isForm?{}:{'Content-Type':'application/json'}})});
  const data = await response.json().catch(()=>({detail:`HTTP ${response.status}`}));
  if (!response.ok) throw new Error(errorMessage(data.detail || data.error));
  return data;
}
export const assetUrl = id => `/api/assets/${encodeURIComponent(id)}/file`;
