from .doge_alignment import AlignmentCog


async def setup(bot):
    await bot.add_cog(AlignmentCog(bot))
