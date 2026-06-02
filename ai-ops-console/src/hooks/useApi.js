import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../api/services';

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
export function useConversations(status = 'active', channel = null) {
  return useQuery({
    queryKey: ['conversations', status, channel],
    queryFn: () => api.getConversations({ status, channel }),
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
    onSuccess: () => {
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
    onSuccess: () => {
      queryClient.invalidateQueries(['actions']);
    },
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
