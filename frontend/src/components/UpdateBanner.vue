<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { UpdateStatus } from '@/api/types'

const props = defineProps<{ busy: boolean }>()

const update = ref<UpdateStatus | null>(null)
const error = ref('')
const checking = ref(false)

const IDLE_POLL_MS = 5 * 60_000
const ACTIVE_POLL_MS = 1000
// The old process waits this long for the new one before rolling back.
const HANDOVER_LIMIT_MS = 90_000

let timer: number | undefined

const visible = computed(() => {
  const value = update.value
  return !!value?.supported && value.state !== 'none'
})

const percent = computed(() => Math.round((update.value?.progress ?? 0) * 100))

async function refresh() {
  try {
    update.value = await api.getUpdate()
  } catch {
    /* the server may be restarting; the next poll tells */
  }
  schedule()
}

function schedule() {
  window.clearTimeout(timer)
  const state = update.value?.state
  timer = window.setTimeout(refresh, state === 'downloading' ? ACTIVE_POLL_MS : IDLE_POLL_MS)
}

async function install() {
  const target = update.value?.version
  if (!target) return
  error.value = ''
  try {
    update.value = await api.applyUpdate()
  } catch (cause) {
    error.value = (cause as Error).message
    return
  }
  window.clearTimeout(timer)
  // The server stops and the new version takes its place; reload once it answers.
  const current = update.value?.current
  const deadline = Date.now() + HANDOVER_LIMIT_MS
  let wentDown = false
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, ACTIVE_POLL_MS))
    try {
      const { version } = await api.getVersion()
      if (version === target) {
        window.location.reload()
        return
      }
      // Back on the old version after stopping: the new one failed and was undone.
      if (wentDown && version === current) break
    } catch {
      wentDown = true
    }
  }
  // Rolled back: the old version is serving again and says why.
  refresh()
}

// Asks the activation server now, e.g. once the admin has fixed a published version.
async function checkNow() {
  checking.value = true
  error.value = ''
  try {
    update.value = await api.checkUpdate()
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    checking.value = false
  }
  schedule()
}

onMounted(refresh)
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div v-if="visible && update" class="update" :class="{ 'update--required': update.required }">
    <span v-if="update.required" class="update__lead">Esta versión ya no funciona con Renfe.</span>

    <template v-if="update.state === 'downloading'">
      <span>Descargando la versión {{ update.version }} · {{ percent }} %</span>
    </template>

    <template v-else-if="update.state === 'ready'">
      <span>Versión {{ update.version }} lista.</span>
      <button
        class="btn btn--primary btn--small"
        :disabled="props.busy"
        :title="props.busy ? 'Termina la búsqueda primero' : undefined"
        @click="install"
      >
        Actualizar
      </button>
    </template>

    <template v-else-if="update.state === 'applying'">
      <span>Actualizando a la {{ update.version }}…</span>
    </template>

    <template v-else>
      <span>{{ update.error ?? `Versión ${update.version} disponible.` }}</span>
      <button
        v-if="update.state === 'failed'"
        class="btn btn--ghost btn--small"
        :disabled="checking"
        @click="checkNow"
      >
        {{ checking ? 'Comprobando…' : 'Comprobar de nuevo' }}
      </button>
      <a v-if="update.download_url" class="update__link" :href="update.download_url">
        Descargar a mano
      </a>
    </template>

    <span v-if="error" class="error-text">{{ error }}</span>
  </div>
</template>

<style scoped>
.update {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin: 0 0 18px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  border: 1px solid color-mix(in srgb, var(--info) 40%, transparent);
  background: color-mix(in srgb, var(--info) 10%, transparent);
}

.update--required {
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
}

.update__lead {
  font-weight: 600;
}

.update__link {
  color: var(--accent);
  font-weight: 600;
}

.btn--small {
  padding: 6px 12px;
  font-size: 13px;
}
</style>
