package me.manga.kira.sources.contracts

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SourceBaseUrlTest {
    @Test
    fun acceptsHttpOriginsWithPathsAndPorts() {
        assertEquals("example.com", sourceBaseUrlHost("https://Example.COM:8443/api/v1"))
        assertTrue(isValidSourceBaseUrl("http://127.0.0.1:8080/source"))
    }

    @Test
    fun rejectsNonNetworkAndCredentialBearingValues() {
        assertNull(sourceBaseUrlHost("about:about"))
        assertNull(sourceBaseUrlHost("javascript:alert(1)"))
        assertNull(sourceBaseUrlHost("https://user:password@example.com"))
        assertFalse(isValidSourceBaseUrl(""))
    }
}
