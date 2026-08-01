import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../api/services';
import client from '../api/client';

/**
 * Hook for fetching a single ticket by ID (direct fetch, not filtered by status)
 */
export function useTicket(id) {
  return useQuery({
    queryKey: ['ticket', id],
    queryFn: () => api.getTicket(id),
    enabled: !!id,
    retry: 2,
    retryDelay: 1500,
  });
}

/**
 * Hook for fetching conversation list
 */
export function useConversations(status = 'active', storeId = null) {
  return useQuery({
    queryKey: ['conversations', status, storeId],
    queryFn: () => api.getConversations({ status, store_id: storeId }),
    refetchInterval: 10000,
  });
}

/**
 * Hook for fetching a single conversation's messages
 */
export function useMessages(id) {
  return useQuery({
    queryKey: ['messages', id],
    queryFn: () => api.getConversationMessages(id),
    enabled: !!id,
    refetchInterval: 5000,
  });
}

/**
 * Hook for escalated conversations
 */
export function useEscalations() {
  return useQuery({
    queryKey: ['conversations', 'escalated'],
    queryFn: () => api.getEscalations(),
    refetchInterval: 15000,
  });
}

/**
 * Hook for dashboard statistics
 */
export function useStats(channel = null) {
  return useQuery({
    queryKey: ['stats', channel],
    queryFn: () => api.getStats({ channel }),
    refetchInterval: 15000,
  });
}

/**
 * Mutation for marking a conversation as read
 */
export function useMarkRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.markAsRead(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries(['conversations']);
    },
  });
}

/**
 * Mutation for taking over a conversation
 */
export function useTakeover() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.takeoverConversation(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries(['conversations']);
      queryClient.invalidateQueries(['messages', id]);
    },
  });
}

/**
 * Mutation for releasing a conversation to AI
 */
export function useRelease() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.releaseConversation(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries(['conversations']);
      queryClient.invalidateQueries(['messages', id]);
    },
  });
}

/**
 * Hook for fetching the pending actions queue
 */
export function useActions(status = 'pending') {
  return useQuery({
    queryKey: ['actions', status],
    queryFn: () => api.getActions({ status }),
    refetchInterval: 15000,
  });
}

/**
 * Mutation to approve an action
 */
export function useApproveAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.approveAction(id),
    // onSettled (not onSuccess) — a failed approve (e.g. 409 "already
    // cancelled") still changes the action's status server-side (moves it
    // out of "pending"), so the list must refresh on error too. Without
    // this, a failed approve left the stale card on screen looking exactly
    // as actionable as before, even though the backend had already
    // resolved it — the next click would just fail again with no visible
    // change until a manual page refresh.
    onSettled: () => {
      queryClient.invalidateQueries(['actions']);
    },
  });
}

/**
 * Mutation to reject an action
 */
export function useRejectAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }) => api.rejectAction(id, reason),
    onSettled: () => {
      queryClient.invalidateQueries(['actions']);
    },
  });
}

/**
 * Mutation to cancel an order directly from a ticket
 */
export function useCancelOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => api.cancelOrder(id),
    onSuccess: () => {
      queryClient.invalidateQueries(['conversations']);
      queryClient.invalidateQueries(['actions']);
    },
  });
}

/**
 * Hook for quarantine pending count
 */
export function useQuarantineCount() {
  return useQuery({
    queryKey: ['quarantine', 'count'],
    queryFn: async () => {
      const res = await client.get('/api/v1/quarantine').catch(() => ({ data: { pending: 0 } }));
      return res.data?.pending || 0;
    },
    refetchInterval: 15000,
  });
}

/**
 * Mutation for sending an admin message
 */
export function useSendMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, text }) => api.sendAdminMessage(id, text),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries(['messages', id]);
    },
  });
}

/**
 * Current tenant's own profile (email, plan, is_super_admin, ...). Used to
 * gate the Admin nav link and populate the Profile page.
 */
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });
}
