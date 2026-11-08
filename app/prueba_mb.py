from services.album_service import get_album

result = get_album(
    artist="Queen",
    album="Greatest Hits",
)

print(result)