package dev.diamondeq

import org.junit.Assert.assertEquals
import org.junit.Test

class MakeUpGainTest {
    private val profile = Profile("p", "P", "", preampDb = -4.75, filters = emptyList(), loudnessChangeDb = -7.7)

    @Test
    fun fullVolumeLeavesThePreampAlone() = assertEquals(0.0, makeUpGainDb(profile, 0.0), 1e-9)

    @Test
    fun usesOnlyTheHeadroomTheVolumeLeaves() = assertEquals(3.5, makeUpGainDb(profile, 4.0), 1e-9)

    @Test
    fun neverMakesMusicLouderThanWithoutEq() = assertEquals(7.7, makeUpGainDb(profile, 30.0), 1e-9)

    @Test
    fun profilesThatDoNotLowerTheLevelGetNothing() =
        assertEquals(0.0, makeUpGainDb(profile.copy(loudnessChangeDb = 1.2), 30.0), 1e-9)
}
