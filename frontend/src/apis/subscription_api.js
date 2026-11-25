import { apiGet, apiPost, apiDelete } from './base'

/**
 * 资源订阅 API 模块：资源目录、我的申请、超管审批。
 */

const BASE_URL = '/api/subscriptions'

/** 可申请资源目录与当前用户的申请状态 */
export const listSubscriptionCatalog = async () => apiGet(`${BASE_URL}/catalog`)

/** 当前用户的申请记录 */
export const listMySubscriptions = async () => apiGet(`${BASE_URL}/mine`)

/** 提交订阅申请 */
export const submitSubscription = async (resourceType, resourceSlug) =>
  apiPost(BASE_URL, { resource_type: resourceType, resource_slug: resourceSlug })

/** 超管待审批队列 */
export const listPendingSubscriptions = async () => apiGet(`${BASE_URL}/pending`)

/** 超管待审批数量 */
export const getPendingSubscriptionCount = async () => apiGet(`${BASE_URL}/pending/count`)

/** 批准申请 */
export const approveSubscription = async id => apiPost(`${BASE_URL}/${id}/approve`)

/** 驳回申请 */
export const rejectSubscription = async id => apiPost(`${BASE_URL}/${id}/reject`)

/** 撤销已批准的订阅 */
export const revokeSubscription = async id => apiDelete(`${BASE_URL}/${id}`)

/** 个人停止使用资源：只影响自己，不改共享范围 */
export const stopUsingSubscription = async (resourceType, resourceSlug) =>
  apiPost(`${BASE_URL}/stop`, { resource_type: resourceType, resource_slug: resourceSlug })

/** 恢复使用自己停用过的资源 */
export const resumeUsingSubscription = async (resourceType, resourceSlug) =>
  apiPost(`${BASE_URL}/resume`, { resource_type: resourceType, resource_slug: resourceSlug })
