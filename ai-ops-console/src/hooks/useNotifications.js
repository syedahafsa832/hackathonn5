export function useNotifications() {
  const isSupported = 'Notification' in window;
  const isGranted = () => isSupported && Notification.permission === 'granted';

  const requestPermission = async () => {
    if (!isSupported) return false;
    const result = await Notification.requestPermission();
    localStorage.setItem('resolv_notifications', result);
    return result === 'granted';
  };

  const notify = (title, body, urgent = false) => {
    if (!isGranted()) return;
    const n = new Notification(title, {
      body,
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      tag: urgent ? 'urgent' : 'info',
      requireInteraction: urgent,
    });
    n.onclick = () => { window.focus(); n.close(); };
  };

  const hasBeenAsked = () => {
    if (!isSupported) return true;
    return Notification.permission !== 'default' || !!localStorage.getItem('resolv_notifications');
  };

  return { requestPermission, isGranted, notify, isSupported, hasBeenAsked };
}
