import { createContext, useContext, useEffect, useState } from 'react';
import client from '../api/client';

const BrandContext = createContext(null);

export function BrandProvider({ children }) {
  const [brands, setBrands] = useState([]);
  const [activeBrand, setActiveBrand] = useState(null); // null = "All brands"
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('resolv_token');
    if (!token) { setLoading(false); return; }

    client.get('/api/brands').then(res => {
      const d = res.data;
      const list = Array.isArray(d) ? d : d?.brands || [];
      setBrands(list);
      // Restore last selected brand from localStorage
      const saved = localStorage.getItem('resolv_active_brand');
      if (saved) {
        const found = list.find(b => b.id === saved);
        if (found) setActiveBrand(found);
      }
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const switchBrand = (brand) => {
    setActiveBrand(brand);
    if (brand) {
      localStorage.setItem('resolv_active_brand', brand.id);
    } else {
      localStorage.removeItem('resolv_active_brand');
    }
  };

  return (
    <BrandContext.Provider value={{ brands, activeBrand, switchBrand, loading }}>
      {children}
    </BrandContext.Provider>
  );
}

export function useBrand() {
  return useContext(BrandContext);
}
