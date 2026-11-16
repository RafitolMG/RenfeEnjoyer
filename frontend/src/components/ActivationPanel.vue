<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { Activation } from '@/api/types'

const props = defineProps<{ activation: Activation }>()
const emit = defineEmits<{ (e: 'changed', value: Activation): void }>()

const name = ref('')
const contact = ref('')
const sending = ref(false)
const error = ref('')

const status = computed(() => props.activation.status)
const unreachable = computed(() => status.value === null)
const canRequest = computed(
  () => !sending.value && name.value.trim() !== '' && contact.value.trim() !== '',
)

async function send(action: () => Promise<Activation>) {
  sending.value = true
  error.value = ''
  try {
    emit('changed', await action())
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    sending.value = false
  }
}

function requestAccess() {
  if (!canRequest.value) return
  send(() => api.requestActivation(name.value.trim(), contact.value.trim()))
}

const checkAgain = () => send(api.refreshActivation)

// While waiting on the admin, notice the approval without the user having to click.
let timer: number | undefined
onMounted(() => {
  timer = window.setInterval(() => {
    if (status.value === 'pending' && !sending.value) checkAgain()
  }, 30_000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="card activation">
    <h2 class="card__title">Activación</h2>

    <template v-if="status === 'pending'">
      <p class="lead">Solicitud enviada. Falta que el administrador apruebe este equipo.</p>
      <p class="hint">Se comprueba sola cada 30 segundos; también puedes hacerlo ahora.</p>
      <button class="btn btn--primary" :disabled="sending" @click="checkAgain">
        {{ sending ? 'Comprobando…' : 'Comprobar ahora' }}
      </button>
    </template>

    <template v-else-if="status === 'revoked'">
      <p class="lead">El acceso de este equipo ha sido revocado.</p>
      <p class="hint">Si crees que es un error, contacta con el administrador.</p>
      <button class="btn btn--ghost" :disabled="sending" @click="checkAgain">
        Comprobar otra vez
      </button>
    </template>

    <template v-else-if="unreachable">
      <p class="lead">No se puede contactar con el servidor de activación.</p>
      <p class="hint">
        Renfe Enjoyer necesita confirmar que este equipo está autorizado. Comprueba la conexión a
        internet y vuelve a intentarlo.
      </p>
      <button class="btn btn--primary" :disabled="sending" @click="checkAgain">
        {{ sending ? 'Conectando…' : 'Reintentar' }}
      </button>
    </template>

    <form v-else class="request" @submit.prevent="requestAccess">
      <p class="lead">Este equipo todavía no tiene acceso. Solicítalo y el administrador lo revisará.</p>
      <div class="field">
        <label class="field__label" for="activation-name">Nombre</label>
        <input
          id="activation-name"
          v-model="name"
          class="input"
          maxlength="80"
          autocomplete="name"
          required
        />
      </div>
      <div class="field">
        <label class="field__label" for="activation-contact">Contacto (correo o teléfono)</label>
        <input
          id="activation-contact"
          v-model="contact"
          class="input"
          maxlength="120"
          autocomplete="email"
          required
        />
      </div>
      <button type="submit" class="btn btn--primary" :disabled="!canRequest">
        {{ sending ? 'Enviando…' : 'Solicitar acceso' }}
      </button>
    </form>

    <p v-if="error || activation.error" class="error-text">{{ error || activation.error }}</p>
    <p class="machine">Código de este equipo: <code>{{ activation.machine_code }}</code></p>
  </section>
</template>

<style scoped>
.activation {
  max-width: 520px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.request {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.lead {
  margin: 0;
}

.hint,
.machine {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}

.error-text {
  margin: 0;
}

.btn {
  align-self: flex-start;
}
</style>
