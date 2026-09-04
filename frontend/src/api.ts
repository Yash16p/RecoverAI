import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getSummary   = () => api.get('/dashboard/summary')
export const getCases     = (params?: object) => api.get('/dashboard/cases', { params })
export const getCase      = (id: number) => api.get(`/dashboard/cases/${id}`)
export const getTimeline  = (id: number) => api.get(`/dashboard/cases/${id}/timeline`)
export const getBenchmark = () => api.get('/dashboard/benchmark')

export default api
