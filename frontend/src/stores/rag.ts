import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { RagDocument, RagSearchHit } from '@/types'
import {
  deleteDocument as apiDeleteDocument,
  fetchDocuments,
  searchDocuments,
  uploadDocumentStream,
} from '@/api/rag'

export interface UploadProgress {
  filename: string
  stage: string
  message: string
}

type NoticeType = 'info' | 'success' | 'error'

export const useRagStore = defineStore('rag', () => {
  const documents = ref<RagDocument[]>([])
  const isLoading = ref(false)
  const isUploading = ref(false)
  const isSearching = ref(false)
  const searchResults = ref<RagSearchHit[]>([])
  const hasSearched = ref(false)
  const uploadProgress = ref<UploadProgress | null>(null)
  const notice = ref<{ message: string; type: NoticeType }>({
    message: '',
    type: 'info',
  })

  let abortUpload: (() => void) | null = null

  function showNotice(message: string, type: NoticeType = 'info') {
    notice.value = { message, type }
  }

  function clearNotice() {
    notice.value = { message: '', type: 'info' }
  }

  async function loadDocuments() {
    isLoading.value = true
    try {
      const data = await fetchDocuments()
      documents.value = data?.success ? data.documents : []
    } finally {
      isLoading.value = false
    }
  }

  async function upload(file: File) {
    if (!isSupportedFile(file)) {
      showNotice('暂时只支持 .txt / .md / .markdown / .csv / .json / .log 文件。', 'error')
      return false
    }

    if (isUploading.value && abortUpload) {
      abortUpload()
    }

    isUploading.value = true
    uploadProgress.value = {
      filename: file.name,
      stage: 'starting',
      message: '准备上传',
    }

    return new Promise<boolean>((resolve) => {
      abortUpload = uploadDocumentStream(file, {
        onProgress: (event) => {
          uploadProgress.value = {
            filename: file.name,
            stage: event.stage,
            message: event.message,
          }
          if (event.document) {
            upsertDocument(event.document)
          }
        },
        onDone: (event) => {
          finishUpload()
          upsertDocument(event.document)
          showNotice(`${event.document.filename} 已完成向量化。`, 'success')
          resolve(true)
        },
        onError: (event) => {
          finishUpload()
          void loadDocuments()
          showNotice(normalizeUploadError(event.message), 'error')
          resolve(false)
        },
      })
    })
  }

  function cancelUpload() {
    if (abortUpload) {
      abortUpload()
    }
    finishUpload()
    showNotice('已取消上传。', 'info')
  }

  async function removeDocument(documentId: number) {
    const data = await apiDeleteDocument(documentId)
    if (data?.success) {
      documents.value = documents.value.filter(
        (document) => document.document_id !== documentId,
      )
      showNotice('文档已删除。', 'success')
    } else {
      showNotice(data?.message || '删除失败。', 'error')
    }
  }

  async function search(query: string) {
    if (!query.trim()) {
      searchResults.value = []
      hasSearched.value = false
      return
    }

    isSearching.value = true
    hasSearched.value = true
    try {
      const data = await searchDocuments(query)
      searchResults.value = data?.success ? data.hits : []
    } finally {
      isSearching.value = false
    }
  }

  function clearSearch() {
    searchResults.value = []
    hasSearched.value = false
  }

  function upsertDocument(document: RagDocument) {
    const index = documents.value.findIndex(
      (item) => item.document_id === document.document_id,
    )
    if (index >= 0) {
      documents.value[index] = document
    } else {
      documents.value.unshift(document)
    }
  }

  function finishUpload() {
    isUploading.value = false
    uploadProgress.value = null
    abortUpload = null
  }

  function isSupportedFile(file: File) {
    return /\.(txt|md|markdown|csv|json|log)$/i.test(file.name)
  }

  function normalizeUploadError(message: string) {
    return message || '上传失败，请检查后端服务日志。'
  }

  return {
    documents,
    isLoading,
    isUploading,
    isSearching,
    searchResults,
    hasSearched,
    uploadProgress,
    notice,
    showNotice,
    clearNotice,
    loadDocuments,
    upload,
    cancelUpload,
    removeDocument,
    search,
    clearSearch,
  }
})
