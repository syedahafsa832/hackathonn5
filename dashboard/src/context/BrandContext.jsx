import { createContext, useContext, useEffect, useState } from 'react';
import client from '../api/client';

// tResolv is single-store-per-account — this exposes the tenant's one
// brand, not a list to switch between. Previously held an array + a
// switchBrand()/localStorage "last selected brand" concept; removed along
// with the rest of the multi-brand UI (Add Brand, brand switcher).
const BrandContext = createContext(null);

export function BrandProvider({ children }) {
  const [brand, setBrand] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('resolv_token');
    if (!token) { setLoading(false); return; }

    client.get('/api/brands').then(res => {
      const d = res.data;
      const list = Array.isArray(d) ? d : d?.brands || [];
      setBrand(list[0] || null);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  return (
    <BrandContext.Provider value={{ brand, loading }}>
      {children}
    </BrandContext.Provider>
  );
}

export function useBrand() {
  return useContext(BrandContext);
}
