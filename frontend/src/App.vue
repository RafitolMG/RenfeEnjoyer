<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import logoUrl from '@/assets/logo.webp'
import type { Activation, JourneyType, Profile } from '@/api/types'
import ActivationPanel from '@/components/ActivationPanel.vue'
import JobMonitor from '@/components/JobMonitor.vue'
import ProfileDialog from '@/components/ProfileDialog.vue'
import SearchForm from '@/components/SearchForm.vue'
import { useJobStream } from '@/composables/useJobStream'
import { occupiesBrowser } from '@/jobState'

const { status, events, connected } = useJobStream()

const profiles = ref<Profile[]>([])
const selectedProfileId = ref<number | null>(null)
const dialogOpen = ref(false)
const error = ref('')
const sessionVerifiedAt = ref<string | null>(null)
const activation = ref<Activation | null>(null)
const activationLoaded = ref(false)

// Only the Windows executable requires activation; a checkout never sees this screen.
const locked = computed(() => !!activation.value?.required && !activation.value.authorized)

// The refusal that locked the app is stale once it is unlocked again.
watch(locked, (isLocked) => {
  if (!isLocked) error.value = ''
})

// Approved, but the last check failed: the stored license is carrying the app for now.
const activationNotice = computed(() => {
  const value = activation.value
  if (!value?.required || !value.authorized || !value.error || !value.expires_at) return ''
  const until = new Date(value.expires_at).toLocaleDateString('es-ES', {
    day: '2-digit',
    month: '2-digit',
  })
  return `Sin conexión con el servidor de activación · válido hasta el ${until}`
})

async function loadActivation() {
  try {
    activation.value = await api.getActivation()
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    activationLoaded.value = true
  }
}

const sessionLabel = computed(() =>
  sessionVerifiedAt.value
    ? `Sesión verificada el ${new Date(sessionVerifiedAt.value).toLocaleString('es-ES', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })}`
    : 'Sin sesión iniciada',
)

const busy = computed(() => occupiesBrowser(status.value.state))

const SELECTED_PROFILE_KEY = 'renfe-enjoyer:profile'

function rememberedProfileId(): number | null {
  try {
    const stored = Number(localStorage.getItem(SELECTED_PROFILE_KEY))
    return Number.isInteger(stored) && stored > 0 ? stored : null
  } catch {
    return null
  }
}

watch(selectedProfileId, (id) => {
  try {
    if (id === null) localStorage.removeItem(SELECTED_PROFILE_KEY)
    else localStorage.setItem(SELECTED_PROFILE_KEY, String(id))
  } catch {
    /* storage blocked: the selection just is not remembered */
  }
})

/** The profile of the search in progress, so the header talks about the same one. */
function runningProfileId(): number | null {
  const username = status.value.search?.username
  if (!username || !occupiesBrowser(status.value.state)) return null
  return profiles.value.find((p) => p.username === username)?.id ?? null
}

async function loadProfiles() {
  try {
    profiles.value = await api.listProfiles()
    const exists = (id: number | null) => profiles.value.some((p) => p.id === id)
    if (!exists(selectedProfileId.value)) {
      const remembered = rememberedProfileId()
      selectedProfileId.value =
        runningProfileId() ??
        (exists(remembered) ? remembered : (profiles.value[0]?.id ?? null))
    }
  } catch (cause) {
    error.value = (cause as Error).message
  }
}

// A search started here or in another tab, or found running on load: follow it.
watch(
  () => status.value.search?.username,
  () => {
    const running = runningProfileId()
    if (running !== null) selectedProfileId.value = running
  },
)

/** Fire a job action; the resulting state arrives through the job stream. */
async function run(action: () => Promise<unknown>) {
  error.value = ''
  try {
    await action()
  } catch (cause) {
    error.value = (cause as Error).message
  }
}

function startSearch(input: { journey_type: JourneyType; date: string }) {
  const userId = selectedProfileId.value
  if (userId === null) return
  // A refusal may mean this computer was revoked meanwhile; re-read where it stands.
  run(() => api.startJob({ user_id: userId, ...input }).catch(async (cause) => {
    await loadActivation()
    throw cause
  }))
}

async function loadSession() {
  const id = selectedProfileId.value
  if (id === null) {
    sessionVerifiedAt.value = null
    return
  }
  try {
    sessionVerifiedAt.value = (await api.getSession(id)).verified_at
  } catch {
    sessionVerifiedAt.value = null
  }
}

async function clearSession() {
  const id = selectedProfileId.value
  if (id === null) return
  if (!window.confirm('¿Cerrar la sesión guardada de Renfe? La próxima búsqueda pedirá login.')) {
    return
  }
  error.value = ''
  try {
    await api.clearSession(id)
  } catch (cause) {
    error.value = (cause as Error).message
  }
  await loadSession()
}

watch(selectedProfileId, loadSession)

// The bot stores a session when it logs in, so re-check whenever a job ends.
watch(
  () => status.value.state,
  (state) => {
    if (!occupiesBrowser(state)) loadSession()
  },
)

onMounted(() => {
  loadActivation()
  loadProfiles()
  loadSession()
})
</script>

<template>
  <div class="shell">
    <header class="masthead">
      <div class="brand">
        <img class="brand__mark" :src="logoUrl" alt="" width="40" height="40" />
        <h1 class="brand__name">Renfe Enjoyer</h1>
      </div>

      <div v-if="!locked" class="session">
        <span v-if="activationNotice" class="session__state session__state--warn">
          {{ activationNotice }}
        </span>
        <span class="session__state">
          {{ sessionLabel }}
        </span>
        <button v-if="sessionVerifiedAt" class="btn btn--ghost btn--small" @click="clearSession">
          Cerrar sesión
        </button>
      </div>
    </header>

    <!-- While locked the activation panel already says why. -->
    <p v-if="error && !locked" class="error-text banner">{{ error }}</p>

    <ActivationPanel
      v-if="locked && activation"
      :activation="activation"
      @changed="(value) => (activation = value)"
    />

    <main v-else-if="activationLoaded" class="layout">
      <SearchForm
        v-model:selected-profile-id="selectedProfileId"
        :profiles="profiles"
        :busy="busy"
        @submit="startSearch"
        @manage="dialogOpen = true"
      />
      <JobMonitor
        :status="status"
        :events="events"
        :connected="connected"
        @stop="run(api.stopJob)"
        @release="run(api.releaseJob)"
        @code="(value) => run(() => api.submitCode(value))"
        @train="(departure) => run(() => api.chooseTrain(departure))"
      />
    </main>

    <ProfileDialog
      v-if="dialogOpen"
      :profiles="profiles"
      @close="dialogOpen = false"
      @changed="loadProfiles"
    />
  </div>
</template>

<style scoped>
.shell {
  max-width: 1080px;
  margin: 0 auto;
  padding: 40px 24px 64px;
}

.masthead {
  margin-bottom: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.session {
  display: flex;
  align-items: center;
  gap: 10px;
}

.session__state {
  font-size: 13px;
  color: var(--text-muted);
}

.session__state--warn {
  color: var(--warning);
}

.btn--small {
  padding: 6px 12px;
  font-size: 13px;
}

.brand__mark {
  width: 40px;
  height: 40px;
  border-radius: 11px;
  object-fit: cover;
  box-shadow: 0 0 0 1px var(--border-strong);
}

.brand__name {
  font-size: 22px;
}

.banner {
  margin: 0 0 18px;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

@media (max-width: 860px) {
  .layout {
    /* A bare 1fr has an implicit min of min-content, so a wide table would stretch
       the page instead of scrolling inside its own container. */
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
